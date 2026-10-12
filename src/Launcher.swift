import SwiftUI
import AppKit
import Darwin

struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.fontWeight(.medium).padding(.horizontal, 16).padding(.vertical, 10)
            .foregroundStyle(.white)
            .background(Color(red: 0.16, green: 0.43, blue: 0.39)
                .opacity(enabled ? (configuration.isPressed ? 0.8 : 1) : 0.4),
                in: RoundedRectangle(cornerRadius: 9))
    }
}

struct Session: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var homepage: String
    var note: String
    var createdAt: Date
    var lastOpened: Date?
    var archived: Bool = false
}

struct WorkerStatus: Decodable {
    var phase: String
    var pid: Int32
    var browser_pid: Int32?
    var error: String?
    var tabs: Int
}

@MainActor final class SessionStore: ObservableObject {
    @Published var sessions: [Session] = []
    @Published var statuses: [String: WorkerStatus] = [:]
    @Published var selection: String?
    @Published var error: String?
    @Published var search = ""
    @Published var showArchived = false
    private var processes: [String: Process] = [:]
    private var inputs: [String: Pipe] = [:]
    private var logs: [String: FileHandle] = [:]
    private var timer: Timer?
    private let recycle: (URL) throws -> Void
    private let resources = Bundle.main.resourceURL!
    let root: URL

    init(recycle: @escaping (URL) throws -> Void = {
        try FileManager.default.trashItem(at: $0, resultingItemURL: nil)
    }) {
        self.recycle = recycle
        if let configured = ProcessInfo.processInfo.environment["CAMOUFOX_SESSIONS_DATA_DIR"] {
            root = URL(fileURLWithPath: configured, isDirectory: true)
        } else {
            root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("Camoufox Sessions", isDirectory: true)
        }
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            if FileManager.default.fileExists(atPath: root.appendingPathComponent("sessions.json").path) {
                sessions = try JSONDecoder().decode([Session].self,
                    from: Data(contentsOf: root.appendingPathComponent("sessions.json")))
            }
        } catch { self.error = "无法读取会话记录：\(error.localizedDescription)" }
        selection = sessions.first(where: { !$0.archived })?.id
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    var visible: [Session] {
        sessions.filter { $0.archived == showArchived &&
            (search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) ||
                $0.note.localizedCaseInsensitiveContains(search)) }
    }
    var selected: Session? { sessions.first { $0.id == selection } }
    var activeCount: Int { sessions.filter { isActive($0.id) }.count }
    func profile(_ id: String) -> URL { root.appendingPathComponent("profiles/\(id)", isDirectory: true) }
    func isActive(_ id: String) -> Bool {
        if let process = processes[id], process.isRunning { return true }
        guard let status = statuses[id], ["starting", "running", "closing"].contains(status.phase) else { return false }
        return kill(status.pid, 0) == 0
    }
    func statusLabel(_ id: String) -> String {
        switch statuses[id]?.phase {
        case "starting": return "正在打开"
        case "closing": return "正在保存"
        case "running" where isActive(id): return "已打开"
        case "error": return "需要重试"
        default: return "未打开"
        }
    }
    func refresh() {
        var result: [String: WorkerStatus] = [:]
        for session in sessions {
            if let data = try? Data(contentsOf: profile(session.id).appendingPathComponent("status.json")),
               var status = try? JSONDecoder().decode(WorkerStatus.self, from: data) {
                if ["starting", "running", "closing"].contains(status.phase) && kill(status.pid, 0) != 0 {
                    status.phase = "closed"
                }
                result[session.id] = status
            }
        }
        statuses = result
    }
    func save() throws {
        let data = try JSONEncoder().encode(sessions)
        let path = root.appendingPathComponent("sessions.json")
        try data.write(to: path, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
    }
    func upsert(_ existing: Session?, name: String, homepage: String, note: String) -> Bool {
        let cleaned = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let home = homepage.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { error = "请给会话起一个名字。"; return false }
        guard !sessions.contains(where: { $0.id != existing?.id && $0.name == cleaned }) else {
            error = "这个名字已经使用，请换一个名字。"; return false
        }
        let target = home.isEmpty ? "about:blank" : home
        guard target == "about:blank" ||
            (URL(string: target)?.host != nil && ["http", "https"].contains(URL(string: target)?.scheme ?? "")) else {
            error = "首页需要完整的 http:// 或 https:// 地址。"; return false
        }
        let previous = sessions
        var item = existing ?? Session(id: UUID().uuidString.lowercased(), name: cleaned,
            homepage: target, note: note, createdAt: Date())
        item.name = cleaned; item.homepage = target; item.note = note
        if let index = sessions.firstIndex(where: { $0.id == item.id }) { sessions[index] = item }
        else { sessions.append(item) }
        do { try save(); selection = item.id; return true }
        catch { sessions = previous; self.error = "保存失败：\(error.localizedDescription)"; return false }
    }
    func archive(_ item: Session) {
        guard !isActive(item.id), let index = sessions.firstIndex(where: { $0.id == item.id }) else { return }
        let previous = sessions
        sessions[index].archived.toggle()
        do { try save(); selection = visible.first?.id }
        catch { sessions = previous; self.error = "保存失败：\(error.localizedDescription)" }
    }
    func deleteArchived(_ item: Session) {
        guard let current = sessions.first(where: { $0.id == item.id }), current.archived else {
            error = "请先归档这个会话，再删除。"; return
        }
        guard UUID(uuidString: current.id) != nil else {
            error = "会话目录无效，无法删除。"; return
        }
        refresh()
        guard !isActive(current.id) else { error = "请先关闭这个会话，再删除。"; return }
        let directory = profile(current.id)
        let hasDirectory = FileManager.default.fileExists(atPath: directory.path)
        var lock: Int32 = -1
        if hasDirectory {
            lock = Darwin.open(directory.appendingPathComponent("worker.lock").path,
                O_CREAT | O_RDWR | O_NOFOLLOW, mode_t(0o600))
            guard lock >= 0 else { error = "无法检查会话是否正在使用，未删除。"; return }
            guard flock(lock, LOCK_EX | LOCK_NB) == 0 else {
                Darwin.close(lock)
                error = "这个会话正在使用，请关闭后再删除。"; return
            }
        }
        defer { if lock >= 0 { Darwin.close(lock) } }
        let previous = sessions
        sessions.removeAll { $0.id == current.id }
        do { try save() }
        catch { sessions = previous; self.error = "保存失败，未删除会话：\(error.localizedDescription)"; return }
        do {
            if hasDirectory { try recycle(directory) }
        } catch {
            sessions = previous
            let reason = error.localizedDescription
            do { try save(); self.error = "移入废纸篓失败，会话已保留：\(reason)" }
            catch { self.error = "数据仍保留，但恢复会话记录失败：\(error.localizedDescription)" }
            return
        }
        statuses[current.id] = nil
        processes[current.id] = nil; inputs[current.id] = nil
        try? logs.removeValue(forKey: current.id)?.close()
        if selection == current.id { selection = visible.first?.id }
    }
    func launch(_ item: Session) {
        guard !item.archived else { error = "请先恢复归档会话，再打开。"; return }
        guard !isActive(item.id) else { focus(item.id); return }
        let process = Process()
        process.executableURL = resources.appendingPathComponent("runtime/bin/python")
        process.arguments = [resources.appendingPathComponent("session_worker.py").path,
            "--resources", resources.path, "--profile", profile(item.id).path,
            "--homepage", item.homepage]
        process.environment = ProcessInfo.processInfo.environment.merging(["PYTHONUNBUFFERED": "1"]) { _, new in new }
        let pipe = Pipe()
        process.standardInput = pipe
        do {
            try FileManager.default.createDirectory(at: profile(item.id), withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let logURL = profile(item.id).appendingPathComponent("worker.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            let log = try FileHandle(forWritingTo: logURL)
            process.standardOutput = log; process.standardError = log
            process.terminationHandler = { [weak self] ended in
                Task { @MainActor in
                    guard let self else { return }
                    if self.processes[item.id] === ended {
                        self.processes[item.id] = nil; self.inputs[item.id] = nil
                        try? self.logs.removeValue(forKey: item.id)?.close()
                        self.refresh()
                    }
                }
            }
            try process.run()
            processes[item.id] = process; inputs[item.id] = pipe; logs[item.id] = log
            statuses[item.id] = WorkerStatus(phase: "starting", pid: process.processIdentifier, tabs: 0)
            if let index = sessions.firstIndex(where: { $0.id == item.id }) {
                sessions[index].lastOpened = Date(); try save()
            }
        } catch { self.error = "无法打开会话：\(error.localizedDescription)" }
    }
    func command(_ id: String, _ action: String) {
        if let input = inputs[id] {
            do { try input.fileHandleForWriting.write(contentsOf: Data("{\"action\":\"\(action)\"}\n".utf8)) }
            catch { self.error = "会话进程已退出，请重新打开。" }
        } else if action == "close", let pid = statuses[id]?.pid, isActive(id) {
            kill(pid, SIGTERM)
        }
    }
    func close(_ id: String) { command(id, "close") }
    func focus(_ id: String) {
        command(id, "focus")
        if let pid = statuses[id]?.browser_pid {
            NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateAllWindows])
        }
    }
    func reveal(_ item: Session) {
        let directory = profile(item.id)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        NSWorkspace.shared.open(directory)
    }
    func closeAll() { for session in sessions where isActive(session.id) { close(session.id) } }
}

struct SessionEditor: View {
    @EnvironmentObject var store: SessionStore
    @Environment(\.dismiss) var dismiss
    let existing: Session?
    @State var name: String
    @State var homepage: String
    @State var note: String
    init(existing: Session?) {
        self.existing = existing
        _name = State(initialValue: existing?.name ?? "")
        _homepage = State(initialValue: existing?.homepage == "about:blank" ? "" : existing?.homepage ?? "")
        _note = State(initialValue: existing?.note ?? "")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(existing == nil ? "新建会话" : "编辑会话").font(.title2.bold())
            Text("给每个浏览环境一个名字，下次从这里继续。")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 7) {
                Text("会话名称").font(.headline)
                TextField("例如：工作、个人、项目测试", text: $name)
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("首次打开的页面").font(.headline)
                TextField("https://example.com（选填）", text: $homepage)
                Text("以后优先恢复上次打开的标签页。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("备注").font(.headline)
                TextField("用来记住这个会话的用途（选填）", text: $note)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") {
                    if store.upsert(existing, name: name, homepage: homepage, note: note) { dismiss() }
                }.buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.textFieldStyle(.roundedBorder).padding(28).frame(width: 440)
    }
}

struct DetailView: View {
    @EnvironmentObject var store: SessionStore
    let session: Session
    var edit: () -> Void
    var delete: () -> Void
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .top) {
                    Image(systemName: "square.stack.3d.up.fill")
                        .font(.system(size: 30)).foregroundStyle(Color.accentColor)
                        .frame(width: 68, height: 68)
                        .background(Color.accentColor.opacity(0.10), in: RoundedRectangle(cornerRadius: 19))
                    VStack(alignment: .leading, spacing: 8) {
                        Text(session.name).font(.system(size: 29, weight: .bold))
                        Label(store.statusLabel(session.id), systemImage: store.isActive(session.id) ? "circle.fill" : "circle")
                            .font(.caption).foregroundStyle(store.isActive(session.id) ? .green : .secondary)
                    }
                    Spacer()
                    Button(action: edit) { Label("编辑", systemImage: "pencil") }.buttonStyle(.bordered)
                }
                if !session.note.isEmpty { Text(session.note).foregroundStyle(.secondary) }
                HStack(spacing: 12) {
                    if session.archived {
                        Button("恢复会话") { store.archive(session) }.buttonStyle(PrimaryButtonStyle())
                        Button("删除会话…", role: .destructive, action: delete)
                            .buttonStyle(.bordered).disabled(store.isActive(session.id))
                    } else if store.isActive(session.id) {
                        Button { store.focus(session.id) } label: { Label("切到窗口", systemImage: "arrow.up.forward.app") }
                            .buttonStyle(PrimaryButtonStyle()).controlSize(.large)
                        Button { store.close(session.id) } label: { Label("保存并关闭", systemImage: "stop.circle") }
                            .buttonStyle(.bordered).controlSize(.large)
                            .disabled(store.statuses[session.id]?.phase == "closing")
                    } else {
                        Button { store.launch(session) } label: { Label("打开会话", systemImage: "play.fill") }
                            .buttonStyle(PrimaryButtonStyle()).controlSize(.large)
                    }
                }
                if let error = store.statuses[session.id]?.error {
                    Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.orange)
                        .font(.callout).textSelection(.enabled)
                }
                VStack(alignment: .leading, spacing: 18) {
                    Label("每次打开，都能接着用", systemImage: "arrow.clockwise")
                        .font(.headline)
                    Text("自动保存登录状态和浏览器数据，恢复上次打开的页面。每个会话单独存储，也可以同时打开。")
                        .foregroundStyle(.secondary).lineSpacing(5)
                    Divider()
                    info("首次打开", session.homepage == "about:blank" ? "空白页面" : session.homepage)
                    info("最近打开", session.lastOpened?.formatted(date: .abbreviated, time: .shortened) ?? "还未打开")
                    if store.isActive(session.id), let tabs = store.statuses[session.id]?.tabs {
                        info("当前页面", "\(tabs) 个")
                    }
                    info("创建时间", session.createdAt.formatted(date: .abbreviated, time: .omitted))
                }.padding(24).background(.background, in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(.quaternary, lineWidth: 1))
                HStack {
                    Button { store.reveal(session) } label: { Label("查看会话文件", systemImage: "folder") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                    Spacer()
                    if !session.archived {
                        Button("归档会话") { store.archive(session) }
                            .buttonStyle(.plain).foregroundStyle(.secondary).disabled(store.isActive(session.id))
                    }
                }.font(.callout)
                Text("网站可能要求重新登录。关闭启动器时，会先保存并关闭它打开的浏览器窗口。")
                    .font(.caption).foregroundStyle(.secondary).lineSpacing(4)
            }.padding(34).frame(maxWidth: 760, alignment: .leading)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: .windowBackgroundColor))
    }
    func info(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary).frame(width: 90, alignment: .leading)
            Text(value).textSelection(.enabled)
            Spacer(minLength: 0)
        }.font(.callout)
    }
}

struct ContentView: View {
    @EnvironmentObject var store: SessionStore
    @State private var creating = false
    @State private var editing: Session?
    @State private var deleting: Session?
    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("你的浏览会话").font(.title3.bold())
                    Text("\(store.sessions.filter { !$0.archived }.count) 个会话 · \(store.activeCount) 个已打开")
                        .font(.caption).foregroundStyle(.secondary)
                    TextField("搜索会话", text: $store.search).textFieldStyle(.roundedBorder).padding(.top, 8)
                }.padding(20)
                List(selection: $store.selection) {
                    ForEach(store.visible) { session in
                        HStack(spacing: 10) {
                            Image(systemName: "square.stack.3d.up").foregroundStyle(Color.accentColor)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(session.name).fontWeight(.medium).lineLimit(1)
                                Text(store.statusLabel(session.id)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                            if store.isActive(session.id) { Circle().fill(.green).frame(width: 7, height: 7) }
                        }.padding(.vertical, 6).tag(session.id)
                            .contextMenu {
                                Button("打开会话") { store.launch(session) }.disabled(session.archived)
                                Button("编辑") { editing = session }
                                Button(session.archived ? "恢复" : "归档") { store.archive(session) }.disabled(store.isActive(session.id))
                                if session.archived {
                                    Divider()
                                    Button("删除会话…", role: .destructive) { deleting = session }
                                        .disabled(store.isActive(session.id))
                                }
                            }
                    }
                }.listStyle(.sidebar)
                VStack(spacing: 12) {
                    Button { creating = true } label: { Label("新建会话", systemImage: "plus").frame(maxWidth: .infinity) }
                        .buttonStyle(PrimaryButtonStyle()).controlSize(.large).keyboardShortcut("n")
                    Toggle("查看归档", isOn: $store.showArchived).font(.caption).toggleStyle(.switch)
                        .onChange(of: store.showArchived) { _, _ in store.selection = store.visible.first?.id }
                }.padding(18)
            }.navigationSplitViewColumnWidth(min: 245, ideal: 265, max: 320)
        } detail: {
            if let selected = store.selected {
                DetailView(session: selected, edit: { editing = selected }, delete: { deleting = selected })
            } else {
                VStack(spacing: 18) {
                    Image(systemName: "square.stack.3d.up.fill").font(.system(size: 52)).foregroundStyle(Color.accentColor)
                    Text("给浏览会话一个名字").font(.title.bold())
                    Text("工作、个人、测试，都可以拥有各自的窗口。\n保存一次，下次接着用。")
                        .foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(6)
                    Button("新建会话") { creating = true }.buttonStyle(PrimaryButtonStyle()).controlSize(.large)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }.frame(minWidth: 840, minHeight: 580)
            .sheet(isPresented: $creating) { SessionEditor(existing: nil) }
            .sheet(item: $editing) { SessionEditor(existing: $0) }
            .alert("删除归档会话？", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } })) {
                Button("取消", role: .cancel) { deleting = nil }
                Button("删除", role: .destructive) {
                    if let item = deleting { store.deleteArchived(item) }
                    deleting = nil
                }
            } message: {
                Text("“\(deleting?.name ?? "")”会从列表移除，登录数据、浏览器配置和标签页记录会移到系统废纸篓。")
            }
            .alert("操作未完成", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
                Button("好") { store.error = nil }
            } message: { Text(store.error ?? "") }
    }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var store: SessionStore?
    private var quitPending = false
    func applicationDidFinishLaunching(_ notification: Notification) { NSApp.activate(ignoringOtherApps: true) }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let store, store.activeCount > 0 else { return .terminateNow }
        if quitPending { return .terminateLater }
        quitPending = true
        store.closeAll()
        Task { @MainActor in
            while store.activeCount > 0 {
                try? await Task.sleep(for: .milliseconds(300))
                store.refresh()
            }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main struct LauncherApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = SessionStore()
    var body: some Scene {
        Window("Camoufox Sessions", id: "main") {
            ContentView().environmentObject(store).tint(Color(red: 0.16, green: 0.43, blue: 0.39))
                .onAppear { delegate.store = store }
        }.defaultSize(width: 1000, height: 690)
            .commands {
                CommandGroup(replacing: .newItem) { }
                CommandGroup(after: .appInfo) {
                    Button("打开会话数据目录") { NSWorkspace.shared.open(store.root) }
                }
            }
    }
}
