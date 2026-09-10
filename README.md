# Python Container PDF Signing with Fonts

[![Product Page](https://img.shields.io/badge/Product%20Page-2865E0?style=for-the-badge&logo=appveyor&logoColor=white)](https://github.com/groupdocs-signature/GroupDocs.Signature-Docs)
[![Docs](https://img.shields.io/badge/Docs-2865E0?style=for-the-badge&logo=Hugo&logoColor=white)](https://docs.groupdocs.com/signature/python-net/)
[![Blog](https://img.shields.io/badge/Blog-2865E0?style=for-the-badge&logo=WordPress&logoColor=white)](https://blog.groupdocs.com/categories/groupdocs.signature-product-family/)
[![Free Support](https://img.shields.io/badge/Free%20Support-2865E0?style=for-the-badge&logo=Discourse&logoColor=white)](https://forum.groupdocs.com/c/signature/13)
[![Temporary License](https://img.shields.io/badge/Temporary%20License-2865E0?style=for-the-badge&logo=rocket&logoColor=white)](https://purchase.groupdocs.com/temp-license/100124)

## 📖 About This Repository

`python-linux-container-pdf-signing` is a runnable Python script that signs a PDF with Latin and CJK text signatures inside a Linux container and verifies both afterwards. It ships two Dockerfiles: `Dockerfile` installs the .NET dependencies plus a font layer, and `Dockerfile.nofonts` keeps the .NET dependencies and drops the fonts, so the failure can be reproduced in one build.

`python:3.11-slim` contains zero font files. GroupDocs.Signature does not substitute a missing family, so on that image every text signature fails until fonts are installed, and clearing `options.font` does not rescue it because the library then requests its own default and fails the same way.

## The Challenge

Two things have to be provisioned in a Python image, not one. The binding runs on .NET, so the container needs `libicu` and `libssl1.1` before anything imports; `libssl1.1` is not in bookworm, so it comes from a pinned Debian snapshot, which is the approach the [Running in Docker](https://docs.groupdocs.com/signature/python-net/getting-started/running-in-docker/) guide documents. Fonts are the second layer, and they are the one people forget because nothing in the traceback says "font" until a signature is attempted.

Then there is the type trap, which cost me most of an afternoon before I saw it. `SignatureFont.size` maps to a .NET float, and passing an int raises `numeric argument expected, got 'int'`. That error surfaces from inside the probe loop, so every candidate family appears to fail and the run looks like a font-provisioning problem when it is a one-character fix.

**What is GroupDocs.Signature for Python via .NET?**

A signing library that applies and verifies text, image, barcode, QR and digital signatures across PDF, Office and image formats through a single `Signature` object. Key features used here:

- Predicate-free `sign(path, [options])` that returns a result carrying `succeeded`
- `TextSignOptions` for text signatures, with an optional `SignatureFont`
- `TextVerifyOptions` with `match_type` and `all_pages` for reading the result back
- Context-manager support, so `with signature.Signature(path) as sign:` closes the handle

One documentation note worth recording: the docs state that Python via .NET has limited Linux support and omit Signature from the Linux-ready package list. On `groupdocs-signature-net==26.1` this sample signed and verified successfully in `python:3.11-slim`, CJK included. Treat the list as stale rather than authoritative, and test your own version.

## Prerequisites

- **Python 3.11** - the container base is `python:3.11-slim`; the wheel caps below CPython 3.14
- **groupdocs-signature-net 26.1** - pinned in `requirements.txt`
- **.NET runtime dependencies** - `libicu67` and `libssl1.1`, installed from a pinned snapshot in the Dockerfile
- **Licence (optional)** - mount it and pass `LIC_PATH` rather than baking a path into the image

## Repository Structure

```
python-linux-container-pdf-signing/
│
├── sign_in_docker_fonts_demo.py
├── requirements.txt
├── Dockerfile
├── Dockerfile.nofonts
├── .dockerignore
└── documents/
    └── sample.pdf
```

- **sign_in_docker_fonts_demo.py** - the whole sample: inventory, probing, resolution, signing, verification
- **requirements.txt** - pins `groupdocs-signature-net==26.1`
- **Dockerfile** - .NET dependencies, then the font layer, then the app
- **Dockerfile.nofonts** - the same image with the font layer removed
- **documents/sample.pdf** - the input

## Code Examples

### Returns the font files visible in the standard system and per-user font directories

The inventory runs before any signing so the log distinguishes "this image has no fonts" from "this image has fonts under other family names". It walks the Linux, Windows and macOS locations and ignores what is not there.

```python
home = os.path.expanduser("~")
roots = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.join(home, ".fonts"),
    os.path.join(home, ".local", "share", "fonts"),
    "/System/Library/Fonts",
    "/Library/Fonts",
]
windir = os.environ.get("WINDIR")
if windir:
    roots.append(os.path.join(windir, "Fonts"))
```

The walk itself is a plain `os.walk` filtered by extension, which is enough to answer the only question that matters at this point: how many, and roughly what.

### Attempts a throwaway signature with one font family

The probe is the load-bearing helper. It signs into the temp directory with a single family and turns the outcome into a value rather than an exception, so the caller can loop over candidates without a try block per iteration.

```python
scratch = os.path.join(tempfile.gettempdir(), f"gd-font-probe-{uuid.uuid4().hex}.pdf")
try:
    with signature.Signature(source_path) as sign:
        options = TextSignOptions()
        options.text = "probe"
        options.left = 10
        options.top = 10
        options.width = 60
        options.height = 20
        font = SignatureFont()
        font.family_name = family_name
        font.size = 10.0
        options.font = font
        sign.sign(scratch, [options])
    return None
except Exception as ex:  # the binding surfaces font errors as a generic exception
    return str(ex)
finally:
    if os.path.exists(scratch):
        os.remove(scratch)
```

Note `font.size = 10.0`. The binding maps size to a .NET float and rejects an int, and because the rejection happens inside the probe it masquerades as a font resolution failure. The broad `except Exception` is deliberate too: the Python binding surfaces font errors as a generic proxy exception rather than a typed one.

### Returns the first candidate family GroupDocs can actually use

Resolution loops the probe over an ordered list. `DejaVu Sans` is first because that is what the font layer installs; `Arial` and `Verdana` are there for a developer running the script on Windows.

```python
for candidate in candidates:
    if try_family(source_path, candidate) is None:
        return candidate
return None
```

Returning `None` instead of raising is what lets the caller treat a missing Latin family as fatal and a missing CJK family as a skipped signature.

### Builds a text signature option set, attaching a font only when a family was resolved

Geometry is unconditional, the font is not. Naming a family the image does not have is exactly what produces the error this project is about.

```python
options = TextSignOptions()
options.text = text
options.left = 50
options.top = top
options.width = 280
options.height = 40
if family_name:
    font = SignatureFont()
    font.family_name = family_name
    # float, not int: the binding maps size to a .NET float and rejects an int with
    # "numeric argument expected, got int".
    font.size = 16.0
    options.font = font
return options
```

### Signs the document with a text signature per resolved family

Both signatures go into one list and one `sign` call. The CJK entry is appended only when a CJK family resolved, so an image with Latin coverage only still produces a valid document.

```python
with signature.Signature(source_path) as sign:
    options = [build_text_options(LATIN_TEXT, latin_family, 50)]
    # Without a CJK-capable font the glyphs cannot be embedded at all, so skip rather than fail.
    if cjk_family:
        options.append(build_text_options(CJK_TEXT, cjk_family, 120))
    result = sign.sign(output_path, options)
    return len(result.succeeded)
```

`len(result.succeeded)` is what to log: 2 on the font image, 1 when only Latin resolved.

### Verifies that the signed document carries a text signature matching the expected value

This is where the Python sample differs from the .NET and Java ones. Instead of searching for signatures and comparing strings, it verifies against an expected value with `TextVerifyOptions`.

```python
with signature.Signature(signed_path) as sign:
    options = TextVerifyOptions()
    options.text = expected_text
    options.match_type = gsd.TextMatchType.CONTAINS
    options.all_pages = True
    result = sign.verify(options)
    return len(result.succeeded)
```

`CONTAINS` matters in evaluation mode, where the library adds its own trial text to the page: an exact match would fail on a document that is otherwise correct.

### Which Dockerfile should I build first?

Build `Dockerfile.nofonts` first, once. It takes a minute and shows the exact failure your future self will be debugging: zero fonts on disk, every candidate family rejected, and a non-zero exit with the minimum fix printed. Then build `Dockerfile` and compare. Seeing both outputs side by side is worth more than any explanation of why the font layer is mandatory.

## Related Topics to Explore

If you're deploying GroupDocs.Signature for Python via .NET into containers, the following articles may be helpful:

* **Step-by-step use case guide in the documentation** - The six functions in order, with the container specifics: [Read the article →](https://docs.groupdocs.com/signature/python-net/use-cases/signing-documents-linux-container-fonts/)

* **In-depth blog article about this project** - Building the script one step at a time, including the float-size trap: [Read the article →](https://blog.groupdocs.com/signature/signing-documents-linux-container-fonts-python-net/)

* **Running GroupDocs.Signature for Python in Docker** - The .NET dependency layer this sample builds on, including the pinned snapshot for libssl1.1: [Read the article →](https://docs.groupdocs.com/signature/python-net/getting-started/running-in-docker/)

* **Installation** - Package names and supported Python versions: [Read the article →](https://docs.groupdocs.com/signature/python-net/installation/)

* **System requirements** - Platform support, including the Linux notes this sample tested against: [Read the article →](https://docs.groupdocs.com/signature/python-net/system-requirements/)

## Keywords

`linux`, `sign`, `documents`, `pdf`, `docker`, `fonts`, `groupdocs signature`, `python signing`, `python via net`, `text signature`, `container fonts`, `fontconfig`, `fonts-dejavu-core`, `fonts-noto-cjk`, `cjk signature`, `SignatureFont`, `TextSignOptions`, `TextVerifyOptions`, `python 3.11-slim`, `libssl1.1`, `libicu`, `font resolution`, `pdf signing linux`, `dockerfile`
