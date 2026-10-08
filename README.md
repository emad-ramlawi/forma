# Forma for Linux

Fill PDF/XFA forms and optionally sign by drawing, typing, or importing an image. Every save creates a new copy. QR codes are supported.

Tested with the Canadian government's **PPTC 042 (08-2026)** PDF form, including form filling, QR code updates, editable saves, and signed exports.

```sh
git clone https://github.com/emad-ramlawi/forma.git
cd forma
./dist/forma
```

The single executable includes Python, dependencies, and application assets. Requires Linux x86_64, glibc 2.34 or newer, and an installed browser. Use **Open a PDF** to begin.

Update with `git pull`, then run `./dist/forma` again. To run the source instead, install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use `./forma`.

[Project documentation](project.md) covers building, testing, Git updates, compatibility, and troubleshooting. [MIT license](LICENSE).
