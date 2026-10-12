# Third-party components

The MIT license in this repository covers this launcher's original source code,
documentation, and icon generator. It does not replace dependencies' licenses.

- [Camoufox browser](https://github.com/daijro/camoufox): MPL-2.0, with Firefox
  and other third-party components retaining their own notices. The build script
  downloads the unmodified upstream browser; its sources are available upstream.
- [Camoufox Python library](https://pypi.org/project/camoufox/0.5.8/): MIT.
- [Playwright](https://github.com/microsoft/playwright-python): Apache-2.0.
- [CPython](https://github.com/python/cpython): Python Software Foundation license,
  with additional component notices included in the standalone runtime.
- [uv](https://github.com/astral-sh/uv): MIT or Apache-2.0; used as a build tool,
  not bundled in the application.

Python dependencies installed during the build retain their distribution metadata
and license files. Browser and runtime files are downloaded or copied locally into
the ignored `build/` directory; they are not checked into this source repository.
If distributing app binaries, preserve those files and the original upstream notices.
