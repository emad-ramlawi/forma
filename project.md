# Forma project documentation

## Purpose and distribution

Forma is an open-source Linux app focused on filling PDF/XFA forms and optional visual electronic signatures. It always saves a separate copy and preserves the original document.

The repository contains both source code and a ready-to-run executable. The project root is the source directory; `dist/` contains exactly one client file, `forma`. Updates are distributed by pushing the source and rebuilt binary together to Git. GitHub releases and tar.gz downloads are not part of this workflow.

Repository: `git@github.com:emad-ramlawi/forma.git` for maintainers, or `https://github.com/emad-ramlawi/forma.git` for clients cloning over HTTPS. The local Git branch is `main`, and `origin` uses the SSH URL. The maintainer handles commits and pushes.

## Client quick start

```sh
git clone https://github.com/emad-ramlawi/forma.git
cd forma
./dist/forma
```

Clients do not need Python, uv, pip, Node, or the source files to run the binary. They can also copy just `dist/forma` to another directory or machine with compatible Linux libraries and run it there:

```sh
chmod +x forma
./forma
```

Git preserves its executable permission. `chmod +x` is useful if a download or copy tool removes that permission.

The current binary targets **Linux x86_64 using glibc 2.34 or newer** and the distribution's libgcc/libstdc++ libraries. It requires an installed graphical browser, such as Chromium, Chrome, Brave, or Firefox, and a desktop session for normal use. ARM, Windows, macOS, and Alpine/musl need separate builds. The recorded native symbol requirement is a lower bound; cross-distribution compatibility has not been certified. `build-info.json` records the actual build's library requirements and inventory.

Chromium-based browsers open an isolated app window with a temporary browser profile. Closing its last window stops Forma’s server, and Ctrl+C or SIGTERM stops both Forma and its own browser processes. Existing browser sessions remain independent. The temporary profile is removed on normal shutdown. Otherwise `xdg-open` opens the default browser: a page-close notification stops the server after a three-second reload grace period. This fallback depends on the browser delivering the notification; use Ctrl+C if it does not. `--no-browser` keeps the server running until Ctrl+C or SIGTERM, even when a manually opened tab closes. The browser remains external to the executable.

### Update the client

Close the running app, then:

```sh
git pull --ff-only
./dist/forma
```

The maintainer must rebuild the binary when changing source or dependencies. A source edit does not update an already-built executable. Copying just the binary means manually replacing it with the updated file.

### Command-line options

```sh
./dist/forma                         # Start with no document open
./dist/forma /path/to/form.pdf       # Open only this explicitly requested PDF
./dist/forma --output /path/to/copies # Starting folder for Save As
./dist/forma --no-browser            # Print the authenticated local URL
./dist/forma --port 8765             # Optional fixed loopback port
./dist/forma --help
```

Startup does not scan nearby folders. Use **Open a PDF**, Ctrl+O, or drag and drop. Source mode supports the same options with `./forma`.

## Filling and saving

Fill the highlighted fields directly. **Save editable copy** preserves supported PDF fields and XFA data and works without a signature.

Both **Save editable copy** and **Export signed PDF** ask for a folder and filename on every action. Forma uses `kdialog` or `zenity` when installed and otherwise offers an in-app folder chooser. `--output` is only an initial folder hint; it never saves automatically. After a successful save, the next chooser starts in that selected folder.

Existing files, symlinks, and originals cannot be overwritten. Choose another name when a file already exists. Cancelling writes nothing and leaves edits and signatures in memory. There is no background save to disk. Unsaved changes produce a warning before leaving or opening another document.

## Optional signatures

**Add signature** opens a two-step wizard:

1. Draw with a mouse or pen, type a name, or import PNG/JPG/WebP artwork. Typed signatures offer handwriting and classic italic styles. Imported scans can have their white background removed.
2. Click the page to place the artwork. Drag to move it, resize from its corner, and use the sidebar or Delete to remove it. Several signatures can be placed on different pages.

**Export signed PDF** saves exactly one fixed PDF containing the filled pages, supported QR codes, and signature artwork. Pages are image-based at 144 dpi. No editable companion is saved automatically; use **Save editable copy** separately if wanted. Signature placements are included only in signed export.

Step 2 creates visual electronic signatures. Optional step 3 adds certificate-based PDF signing, described below; neither workflow supplies independent identity verification or a remote signing service. Follow each form's signing and submission instructions. Editing can invalidate existing certificate and usage-rights signatures, so retain the original for its original signature status.

## XFA and barcode compatibility

PDF.js renders standard PDF forms and supported XFA layouts. Tested workflows include static-XFA filling, saving and reopening, and a generated pure-XFA form.

**PPTC 042, revision 08-2026, barcode version 1.4** has an explicit QR implementation. Forma updates its barcode while editing and includes it in both editable saves and signed exports. It handles the form's field order, name/address formatting, country codes, birth date, gender, ISO-8859-1 encoding, and checksum. Editable copies contain a vector QR appearance and matching XFA hidden barcode values. Invalid barcode inputs remove the old preview and prevent saving a misleading QR.

The template is fingerprinted so these calculation rules apply only to the tested revision. Other documents may need additional barcode profiles. **Check form** runs selected checks for this revision: missing child identity, birth details, sex, eye colour, height, and home-address fields; populated QR fields are checked for the supported names, dates/age, countries, and postal-code formats. Messages link to their fields. Blank optional mailing-address fields are allowed. These checks do not certify completeness: conditional sections, other pages, supporting documents, and signing/submission instructions still require review. Unfinished forms remain saveable.

Forma does not execute general Adobe XFA JavaScript, FormCalc, dynamic validation, or arbitrary barcode scripts. Some layouts and workflows still require another XFA engine. Use Forma's toolbar instead of embedded Save/Print/Complete buttons. Adobe interoperability has not been independently verified.

Tests decode QR images independently with zbar, including a saved PDF rendered by Poppler and the signed PDF's page image. These checks verify payloads and saved appearances; they do not establish acceptance by a government submission system.

## Repository layout

```text
forma                  Source-mode launcher
forma.py               Local HTTP server, save chooser, and signed PDF assembly
xfa_qr.py              Explicit XFA QR profile, encoding, and editable appearances
web/                   Browser interface, vendored assets, and license notices
scripts/audit.py       Dependency advisory checks and vendor integrity checks
scripts/build.py       Single-executable builder
tests/                 Unit and real-browser checks
dist/forma             Client executable; included in Git
build-info.json        Binary hash, size, build metadata, and native inventory
README.md              Short client introduction
project.md             Full project documentation
SECURITY.md            Audit results, protections, and limitations
LICENSE                MIT license for original code
pyproject.toml         Pinned dependencies and development tools
uv.lock                Resolved dependency lockfile
requirements*.txt      Hashed requirement exports
vendor-manifest.json   Vendored browser asset hashes
security/              Machine-readable dependency audits
testpdf/               Local test PDFs; ignored by Git
build/                 Temporary build output; ignored by Git
```

The source launcher and compiled executable share the name `forma` in different directories. Use `./forma` for source mode and `./dist/forma` for the client binary.

## Source development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and a current browser.

```sh
uv sync --locked
./forma
```

The source launcher runs `uv sync --locked --no-dev` automatically, installing the pinned Python runtime and dependencies on first use. First setup needs internet access. Later runs reuse installed dependencies; source updates are applied from Git. `uv run` restores development tools when running tests, audits, or builds.

The current stack is Python 3.14.8, PDF.js 6.4.299, ReportLab 5.0.1, Pillow 12.3.0, pypdf 6.19.0, cryptography 50.0.2, and pyHanko 0.37.0. The interface uses plain JavaScript and CSS. html2canvas captures supported pure-XFA layouts, and the bundled Caveat font supplies handwritten typed signatures. There is no required Node runtime or cloud service.

## Build and verify the binary

Build on Linux x86_64 with `readelf` from binutils installed:

```sh
uv run pytest -q
uv run python scripts/audit.py
uv run python scripts/build.py
FORMA_TEST_LAUNCHER="$PWD/dist/forma" uv run pytest -q tests/test_browser.py
```

The builder uses pinned PyInstaller 6.22.3 in one-file mode. It bundles Python, runtime dependencies, browser assets, documentation, and license texts into one executable, preserves execute permission, and writes `dist/forma`. Build staging, generated specs, and intermediate output remain under ignored `build/`. The old directory/tar.gz outputs are removed. A successful build replaces the previous binary; a failed packaging step leaves it in place.

The builder removes unused PDF.js viewer files, script sandbox files, QuickJS assets, source maps, locales, and demo PDFs from the bundle. It uses the receiving distribution's libgcc/libstdc++ libraries. It records native library requirements and hashes in root `build-info.json`, including the final binary SHA-256 and size. The embedded inventory can be viewed at `/build-info.json` on the running app's local server; its final executable hash is available in the root manifest.

The single-file bundle extracts its runtime into a private temporary directory at startup and removes it on normal exit. It needs writable temporary space that permits loading/executing its libraries. Startup can be slightly slower than source mode. A force kill can leave an extraction directory behind. This behavior is described in [PyInstaller's one-file documentation](https://pyinstaller.org/en/stable/operating-mode.html#how-the-one-file-program-works).

### Tests and local PDFs

Real-form browser tests use `testpdf/pptc042.pdf`, which remains local and is never included in Git or the binary. Those checks skip when it is absent. QR payload/validation and server tests also run without that document. Install `zbarimg` for independent QR decoding and `pdftoppm` for the additional viewer check. Set `FORMA_TEST_BROWSER` if Chromium is outside PATH. Screenshots are written under ignored `tests/artifacts/`.

The browser checks cover field/XFA round trips, optional draw/type/image signatures, placement, resizing, selected save destinations, cancellation, one-file signed export, QR updates/reopening, original checksums, blank startup, and pure-XFA export. Server checks exercise exclusive creation, source/symlink protection, authorization, and traversal rejection.

## Maintainer Git workflow

The remote is configured as:

```sh
git remote -v
# origin git@github.com:emad-ramlawi/forma.git
```

After changing the source, run the verification/build commands above, review changes, then commit and push both source and `dist/forma`:

```sh
git status --short
git add -A
git diff --cached --stat
git commit -m "Describe the change"
git push -u origin main
```

For later commits, `git push` is enough once upstream is set. Your Git identity and GitHub SSH authentication must already be configured. The project does not perform commits or pushes automatically.

`.gitignore` allows exactly `dist/forma` inside `dist/`, and excludes all other distribution files. It also ignores local PDFs, `testpdf/`, exports, signature artwork folders, virtual environments, caches, build work, generated specs, credentials, editor settings, logs, and temporary files. Documentation, lockfiles, audit evidence, vendored assets, license notices, and the root build manifest remain trackable.

Check the binary's tracking rule with:

```sh
git check-ignore dist/forma         # No output means it is not ignored
```

The builder keeps the binary below [GitHub's ordinary 100 MiB Git-file limit](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github), so this workflow does not require Git LFS. Repeated binary commits grow Git history; clients may use `git clone --depth 1` when they only need the latest source and executable.

## Security and licenses

The app listens only on loopback, checks Host and Origin, requires an unpredictable token for file APIs, restricts application asset paths, and creates saved copies exclusively. PDF scripting and eval are disabled. Static-XFA QR inspection also happens through the authenticated local server; supported XML packets reject DOCTYPE/entity declarations. Native dialogs and browser processes receive an environment without the bundled runtime's library-path overrides.

Filling, signing, QR calculations, and exports stay on the computer. No personal values are sent to a remote service. The browser retains unsaved data in memory; its session key is stored in tab session storage. Source dependency installation and maintainer advisory checks use the internet. The compiled client does not download dependencies at startup.

`SECURITY.md` and `security/` document point-in-time advisory results and limits. Keep the OS and browser updated, refresh pinned dependencies when appropriate, rerun audits/tests, and rebuild the executable. Python/npm audits do not comprehensively assess the bundled interpreter, native libraries, or the user's browser.

Original code uses MIT. Dependencies retain their own licenses, including PDF.js Apache 2.0, html2canvas MIT, and Caveat SIL OFL. **About & compatibility → Third-party licenses** exposes notices inside the running binary, including `runtime-licenses.txt` for Python and bundled Python packages. Project documentation and audits are also embedded in the binary under `/docs/` on its local server.

## Troubleshooting

| Symptom | Action |
|---|---|
| Permission denied when launching | Run `chmod +x dist/forma`; use a filesystem that permits execution. |
| Missing glibc or incompatible executable | Use Linux x86_64 with the recorded library requirements; other platforms require another build. |
| Temporary-directory execution blocked | Set `TMPDIR` to a writable directory on a filesystem that permits execution, then launch again. |
| No window opens | Install/configure a browser, or use `--no-browser` and open the printed URL manually. |
| Source launcher reports missing uv | Install uv, or run the bundled client with `./dist/forma`. |
| Saving reports an existing filename | Choose a new filename; Forma preserves existing files. |
| QR disappears while editing | Finish or correct the input reported by Forma; unsupported characters or invalid dates/postal codes cannot be encoded. |
| Another XFA form has a missing barcode | It may require a new explicit barcode profile or an XFA engine supporting its scripts. |
| Browser says the workspace is unavailable | Keep the terminal process running and reopen the URL printed by the current launcher. |


### Optional step 3: certificate-based digital signing

**Add E-signature**, below Add signature, opens a two-stage certificate wizard. Import a PKCS#12 `.p12`/`.pfx` containing a private key and certificate, or create a local RSA-3072 self-signed certificate (one-year validity, encrypted with a password of at least 10 characters). Review signer, issuer, expiry, and SHA-256 fingerprint, then choose Save As. Creation does not independently establish identity: recipients must explicitly trust a self-signed certificate. Import checks expiry and signing key usage but does not establish issuer trust or revocation status.

The output is one new fixed image-based PDF at 144 dpi, including populated fields, supported QR updates, and any optional visual signatures, with a SHA-256 certificate signature recorded in its PDF signature panel. Visual placement is optional; the certificate itself has no visible stamp. Existing source signatures are not retained by this export. Finish edits before signing; subsequent copies exported by Forma do not preserve prior certificate signatures. There is no trusted timestamp, online revocation check, hardware-token support, or long-term-validation service. Reader trust and recipient acceptance must be assessed separately. Use the original visual-signature export when certificate signing is unnecessary.

Key material and passwords pass only to the authenticated loopback service and are used in memory, never written by Forma or added to session storage. Closing the wizard clears its identity/password fields. Python/JavaScript memory cannot guarantee secure erasure, and operating-system swap/crash dumps remain outside this guarantee. Newly created encrypted identities can be downloaded explicitly for reuse; store the file and password privately. Certificate files and `/certificates/` are ignored by Git. Never force-add private keys. Signing uses pyHanko 0.37.0; all dependencies are pinned in uv.lock, audited, and included in the single executable with license notices.
