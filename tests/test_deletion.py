"""Exercise the real Swift session store with isolated data and recycle failures."""
from pathlib import Path
import argparse
import os
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--scratch', type=Path, required=True)
args = parser.parse_args()
args.scratch.mkdir(parents=True, exist_ok=True)
source = (Path(__file__).resolve().parents[1] / 'src/Launcher.swift').read_text().split('struct SessionEditor: View')[0]
harness = r'''
@main struct DeletionTests {
    @MainActor static func main() throws {
        let fm = FileManager.default
        var trashed: NSURL?
        let store = SessionStore(recycle: { path in
            try fm.trashItem(at: path, resultingItemURL: &trashed)
        })
        defer { if let path = trashed { try? fm.removeItem(at: path as URL) } }
        func create(_ name: String) throws -> Session {
            precondition(store.upsert(nil, name: name, homepage: "", note: ""))
            let item = store.sessions.last!
            let directory = store.profile(item.id)
            try fm.createDirectory(at: directory, withIntermediateDirectories: true)
            try Data(name.utf8).write(to: directory.appendingPathComponent("marker"))
            return item
        }
        func diskIDs() throws -> [String] {
            try JSONDecoder().decode([Session].self,
                from: Data(contentsOf: store.root.appendingPathComponent("sessions.json"))).map(\.id)
        }
        let kept = try create("Keep")
        store.deleteArchived(kept)
        precondition(store.sessions.contains(where: { $0.id == kept.id }) && store.error != nil)
        store.error = nil
        let target = try create("Remove")
        store.archive(target)
        var archived = store.sessions.first { $0.id == target.id }!
        let lockURL = store.profile(target.id).appendingPathComponent("worker.lock")
        let descriptor = Darwin.open(lockURL.path, O_CREAT | O_RDWR, mode_t(0o600))
        precondition(descriptor >= 0 && flock(descriptor, LOCK_EX | LOCK_NB) == 0)
        store.deleteArchived(archived)
        precondition(store.sessions.contains(where: { $0.id == target.id }) && store.error != nil)
        Darwin.close(descriptor)
        store.error = nil
        let statusURL = store.profile(target.id).appendingPathComponent("status.json")
        let status: [String: Any] = ["phase":"running", "pid":getpid(), "tabs":1]
        try JSONSerialization.data(withJSONObject: status).write(to: statusURL)
        store.deleteArchived(archived)
        precondition(store.sessions.contains(where: { $0.id == target.id }) && store.error != nil)
        try fm.removeItem(at: statusURL)
        store.error = nil

        let failing = SessionStore(recycle: { _ in throw NSError(domain:"recycle-test", code:1) })
        failing.deleteArchived(archived)
        let afterFailure = try diskIDs()
        precondition(afterFailure.contains(target.id))
        precondition(fm.fileExists(atPath: store.profile(target.id).appendingPathComponent("marker").path))
        precondition(failing.error != nil)

        let metadata = store.root.appendingPathComponent("sessions.json")
        let backup = store.root.appendingPathComponent("metadata-backup")
        try fm.moveItem(at: metadata, to: backup)
        try fm.createDirectory(at: metadata, withIntermediateDirectories: false)
        store.deleteArchived(archived)
        precondition(store.sessions.contains(where: { $0.id == target.id }) && store.error != nil)
        precondition(fm.fileExists(atPath: store.profile(target.id).appendingPathComponent("marker").path))
        try fm.removeItem(at: metadata)
        try fm.moveItem(at: backup, to: metadata)
        store.error = nil

        store.selection = target.id
        store.showArchived = true
        store.deleteArchived(archived)
        precondition(store.error == nil)
        precondition(!store.sessions.contains(where: { $0.id == target.id }))
        let afterDelete = try diskIDs()
        precondition(!afterDelete.contains(target.id) && afterDelete.contains(kept.id))
        precondition(!fm.fileExists(atPath: store.profile(target.id).path))
        precondition(trashed != nil)
        let recovered = try String(contentsOf: (trashed! as URL).appendingPathComponent("marker"), encoding:.utf8)
        precondition(recovered == "Remove")
        precondition(store.selection == nil)
        precondition(fm.fileExists(atPath: store.profile(kept.id).appendingPathComponent("marker").path))

        let empty = try create("No browser data")
        try fm.removeItem(at: store.profile(empty.id))
        store.archive(empty)
        archived = store.sessions.first { $0.id == empty.id }!
        store.deleteArchived(archived)
        precondition(!store.sessions.contains(where: { $0.id == empty.id }))
        print("{\"archived_delete\":\"passed\",\"macos_trash_data\":\"passed\",\"unarchived_guard\":\"passed\",\"active_guard\":\"passed\",\"worker_lock_guard\":\"passed\",\"trash_failure_rollback\":\"passed\",\"save_failure_preserves_data\":\"passed\",\"other_sessions_unchanged\":\"passed\",\"no_directory_delete\":\"passed\"}")
    }
}
'''
with tempfile.TemporaryDirectory(dir=args.scratch.resolve()) as temporary:
    root = Path(temporary)
    swift = root / 'DeletionTests.swift'
    swift.write_text(source + harness)
    binary = root / 'DeletionTests'
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', str(swift), '-o', str(binary),
                    '-framework', 'SwiftUI', '-framework', 'AppKit'], check=True)
    env = {**os.environ, 'CAMOUFOX_SESSIONS_DATA_DIR': str(root / 'data')}
    subprocess.run([str(binary)], env=env, check=True)
