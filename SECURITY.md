# Dependency and security checks

Checked on **2026-10-10**. This describes a point-in-time audit, not a guarantee that no vulnerability exists.

| Component | Version | Purpose |
|---|---|---|
| CPython | 3.14.8 | Bundled runtime, latest stable security maintenance release checked |
| PDF.js / pdfjs-dist | 6.4.299 | Latest stable release, PDF and XFA rendering and form saves |
| ReportLab | 5.0.1 | Latest stable release, fixed signed PDF assembly |
| Pillow | 12.3.0 | Latest stable release, export PNG validation |
| charset-normalizer | 3.5.2 | ReportLab dependency |
| html2canvas | 1.4.1 | Latest published stable version, pure-XFA layout capture |
| Caveat | Google Fonts source snapshot | Bundled typed-signature font under OFL |
| PyInstaller | 6.22.3 | Latest stable release, packaging only |
| Playwright | 1.63.0 | Latest stable release, testing only |
| pytest | 9.1.1 | Latest stable release, testing only |
| pypdf + cryptography | 6.19.0 + 50.0.2 | XFA packet inspection and QR appearances in editable copies; latest stable versions checked |

`pip-audit` found **no known vulnerabilities** in the fully pinned runtime and development dependency lists. The npm advisory bulk endpoint returned **no advisories** for the vendored `pdfjs-dist` and `html2canvas` versions. Machine-readable evidence is in `security/`. Dev transitive dependencies follow their latest compatible versions; Playwright currently constrains pyee to version 13, so the incompatible pyee 14 release is not forced in.

The PDF.js archive's SHA-256 matches the digest in its official GitHub release metadata. The vendored files have individual hashes in `vendor-manifest.json`. [Mozilla's release](https://github.com/mozilla/pdf.js/releases/tag/v6.4.299) is newer than the patched version 6.2.108 listed for [CVE-2026-16633](https://github.com/mozilla/pdf.js/security/advisories/GHSA-hq66-cqwq-w95j). Document scripting and eval are disabled, and a restrictive Content Security Policy is applied. [Python 3.14.8](https://www.python.org/downloads/release/python-3148/) is the current stable maintenance release checked.

Runtime dependencies include ReportLab, Pillow, charset-normalizer, pypdf, the cryptography stack, and pyHanko with its certificate-validation dependencies. PDF.js renders documents in a browser worker; pypdf also inspects static-XFA packets on the authenticated local server and writes QR appearances into editable copies. QR calculations use explicit Python rules for a fingerprinted template, never document JavaScript. XFA XML packets with entity/DOCTYPE declarations are rejected. Barcode previews and calculations are local; no personal field values are sent to remote services.

The server listens only on loopback, checks the Host and Origin, requires an unpredictable key for file APIs, blocks directory traversal for application assets, and creates every saved copy exclusively. Saving requires a chosen destination; the signed-rendering endpoint returns PDF bytes without writing any files. Folder browsing is authenticated and lists folders and PDF names only. Startup never scans nearby folders. Native desktop dialogs run with the frozen runtime's library-path changes removed. No remote fonts, analytics, cloud APIs, or automatic dependency downloads are used. Documents and signature images are held in memory until explicit saving; the session key is kept in tab session storage to support a browser reload.

The client executable is `dist/forma`, a PyInstaller one-file bundle containing Python, application assets, runtime dependencies, documentation, and license texts. It requires Linux x86_64, compatible glibc/system libraries, an installed browser, and an executable temporary filesystem for runtime extraction. It does not install or download dependencies on the client's machine. Source mode uses uv to install the pinned dependencies when needed.

Limits: advisories may be incomplete or change later. Python/npm audits do not comprehensively audit the bundled interpreter, native image libraries, operating system, or the user's browser. PyInstaller can bundle native libraries from the build host; their inventory and the executable hash are recorded in `build-info.json`. Use an updated OS and browser and rebuild when security updates are released. This package has not received an independent penetration test or a cross-distribution compatibility certification.

Certificate signing uses pyHanko locally. PKCS#12 private keys and passwords are processed in memory without application persistence or logging. Self-signed certificates do not establish verified identity. Issuer trust, revocation, trusted timestamps, and existing-signature preservation are not supplied by this fixed-copy export. Certificate/key files are ignored by Git.
