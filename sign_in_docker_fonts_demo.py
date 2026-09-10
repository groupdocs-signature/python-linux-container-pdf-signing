# Topic: Signing documents inside a Linux container - font provisioning, resolving a family that exists, and a working Dockerfile.
# Most container base images ship zero fonts, and GroupDocs.Signature does not substitute a missing one: naming a family that is
# not installed raises an error and produces no document. Leaving the font unset does not help either - the library then asks for
# its own default and fails the same way - so a fontless image cannot apply a text signature at all. This sample inventories the
# fonts on disk, asks the library which candidate families it can actually use, signs a PDF with Latin and CJK text signatures,
# verifies both, and shows the missing-font error inside an except block.
#
# NOTE on Linux support: the GroupDocs docs state that GroupDocs.Signature for Python via .NET has limited Linux support and list
# only comparison/watermark/metadata/merger as Linux-ready PyPI packages. Run the Docker target here to see how it behaves on
# your version rather than assuming; the README records what this sample actually did.

import os
import sys
import uuid
import tempfile

import groupdocs.signature as signature
from groupdocs.signature.options import TextSignOptions, TextVerifyOptions
from groupdocs.signature.domain import SignatureFont
import groupdocs.signature.domain as gsd

DOCS = "documents"
RESULT = "Result"
SOURCE_PDF = os.path.join(DOCS, "sample.pdf")
SIGNED_PDF = os.path.join(RESULT, "signed.pdf")

# Preference order, most portable first. The container installs the first entry of each list;
# the trailing entries are what a Windows or macOS developer box is likely to have instead.
LATIN_CANDIDATES = ("DejaVu Sans", "Liberation Sans", "Arial", "Verdana")
CJK_CANDIDATES = (
    "Noto Sans CJK JP", "Noto Sans CJK SC", "Noto Sans CJK", "Noto Sans JP",
    "MS Gothic", "Yu Gothic", "SimSun", "Malgun Gothic",
)

# A family that exists nowhere, used to show the failure mode on purpose.
ABSENT_FAMILY = "No Such Font Family"

LATIN_TEXT = "Approved by GroupDocs"

# Japanese for "approved". Kept as escapes so this file stays ASCII, and never written to
# stdout - the Windows console codepage here cannot encode it and would crash the sample.
CJK_TEXT = "\u627F\u8A8D\u6E08\u307F"

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".pfb")


def apply_license() -> None:
    # Point this at your .lic file to remove evaluation limits.
    # Get a free temporary licence: https://purchase.groupdocs.com/temporary-license
    license_path = "REPLACE_WITH_YOUR_LICENSE_PATH"

    # In a container the path above is baked in at build time, which is rarely what you want.
    # LIC_PATH lets the licence be mounted and named at run time instead:
    #   docker run --rm -v "/path/to/licences:/lic:ro" -e LIC_PATH=/lic/GroupDocs.Total.lic <image>
    from_env = os.environ.get("LIC_PATH")

    resolved = None
    if os.path.exists(license_path):
        resolved = license_path
    elif from_env and os.path.exists(from_env):
        resolved = from_env

    if resolved:
        signature.License().set_license(resolved)
        print("[license] applied")
    else:
        # Evaluation mode still signs, but it adds its own trial text to the page - which the
        # verify step below will not match. Licence it for a clean run.
        print("[license] no licence set - running in evaluation mode")


def is_container() -> bool:
    """
    Detects whether the process is running inside a container.

    Remarks:
        Checks for the Docker marker file, then for a container runtime named in PID 1's cgroup
        entry, which also catches containerd, podman and Kubernetes. Always False on Windows.
    """
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            text = handle.read()
        return "docker" in text or "containerd" in text or "kubepods" in text
    except OSError:
        return False


def find_font_files() -> list:
    """
    Returns the font files visible in the standard system and per-user font directories.

    Remarks:
        Probes the Linux, Windows and macOS locations in one pass and ignores directories that do
        not exist, so the same call is meaningful on a developer laptop and inside a slim base image.
    """
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

    files = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.lower().endswith(FONT_EXTENSIONS):
                    files.append(os.path.join(dirpath, name))
    return files


def summarise(font_files: list, max_items: int) -> str:
    """
    Builds a short, ASCII-safe sample of the font files found, for logging.

    Remarks:
        Prints distinct file stems rather than resolved family names, and says how many were not
        shown, so a container with two fonts and a laptop with four hundred both give one line.
    """
    if not font_files:
        return "(none - this image has no fonts installed)"

    names = []
    for path in font_files:
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem not in names:
            names.append(stem)
        if len(names) == max_items:
            break

    head = ", ".join(names)
    remaining = len(font_files) - len(names)
    return f"{head} (+{remaining} more)" if remaining > 0 else head


def try_family(source_path: str, family_name: str):
    """
    Attempts a throwaway signature with one font family.

    Remarks:
        Returns None when the family works, otherwise the error message. Writes to a temporary file
        that is always deleted, so probing never touches Result/.
    """
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


def resolve_usable_family(source_path: str, candidates) -> str:
    """
    Returns the first candidate family GroupDocs can actually use, or None if none work.

    Remarks:
        Resolution asks the library rather than guessing from file names. Font files rarely carry
        the family string a caller must pass - Debian's fonts-noto-cjk installs
        NotoSansCJK-Regular.ttc, whose family is "Noto Sans CJK JP" - so a filename match both
        misses real fonts and claims fonts that will not resolve.
    """
    for candidate in candidates:
        if try_family(source_path, candidate) is None:
            return candidate
    return None


def build_text_options(text: str, family_name: str, top: int) -> TextSignOptions:
    """
    Builds a text signature option set, attaching a font only when a family was resolved.

    Remarks:
        The font is left unset when family_name is None; naming a family that is not installed is
        what raises the missing-font error.
    """
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


def sign_with_resolved_fonts(source_path: str, output_path: str, latin_family: str, cjk_family: str) -> int:
    """
    Signs the document with a text signature per resolved family, skipping CJK when none exists.

    Remarks:
        Passing None for a family omits the font entirely so GroupDocs uses its own default rather
        than a name it cannot resolve. Returns the number of signatures written.
    """
    with signature.Signature(source_path) as sign:
        options = [build_text_options(LATIN_TEXT, latin_family, 50)]
        # Without a CJK-capable font the glyphs cannot be embedded at all, so skip rather than fail.
        if cjk_family:
            options.append(build_text_options(CJK_TEXT, cjk_family, 120))
        result = sign.sign(output_path, options)
        return len(result.succeeded)


def verify_text(signed_path: str, expected_text: str) -> int:
    """
    Verifies that the signed document carries a text signature matching the expected value.

    Remarks:
        Scans all pages with TextVerifyOptions and returns the count of matching signatures, which
        is how the sample proves the CJK text survived the round-trip rather than merely appearing to.
    """
    with signature.Signature(signed_path) as sign:
        options = TextVerifyOptions()
        options.text = expected_text
        options.match_type = gsd.TextMatchType.CONTAINS
        options.all_pages = True
        result = sign.verify(options)
        return len(result.succeeded)


def main() -> int:
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(RESULT, exist_ok=True)
    apply_license()

    print("=== GroupDocs.Signature - signing in a container: font report ===")
    print(f"[env] os        : {sys.platform}")
    print(f"[env] container : {'yes' if is_container() else 'no'}")

    font_files = find_font_files()
    print(f"[fonts] font files on disk: {len(font_files)}")
    print(f"[fonts] sample: {summarise(font_files, 6)}")

    if not os.path.exists(SOURCE_PDF):
        print(f"Missing source document: {os.path.abspath(SOURCE_PDF)}", file=sys.stderr)
        return 1

    latin_family = resolve_usable_family(SOURCE_PDF, LATIN_CANDIDATES)
    cjk_family = resolve_usable_family(SOURCE_PDF, CJK_CANDIDATES)
    print(f"[fonts] latin family resolved: {latin_family or '(none - falling back to the platform default)'}")
    print(f"[fonts] cjk family resolved  : {cjk_family or '(none - CJK signature will be skipped)'}")

    # The teaching moment: what actually happens when the image has no fonts.
    print(f"[demo] signing with '{ABSENT_FAMILY}' on purpose...")
    failure = try_family(SOURCE_PDF, ABSENT_FAMILY)
    print(f"[demo] -> {failure or 'no exception - this platform substituted a font instead of failing'}")

    try:
        applied = sign_with_resolved_fonts(SOURCE_PDF, SIGNED_PDF, latin_family, cjk_family)
    except Exception as ex:
        # Reached when the image has no usable font at all. Omitting the font does not help:
        # GroupDocs then asks for its own default family and fails the same way.
        print(f"[sign] FAILED: {ex}", file=sys.stderr)
        print("[sign] this image cannot render text signatures - it has no usable font.", file=sys.stderr)
        print("[sign] there is no code-level workaround: install at least one font in the image.", file=sys.stderr)
        print("[sign] minimum fix: apt-get install -y fonts-dejavu-core (add fonts-noto-cjk for CJK).", file=sys.stderr)
        return 3

    print(f"[sign] text signatures applied: {applied}")

    latin_verified = verify_text(SIGNED_PDF, LATIN_TEXT)
    cjk_verified = verify_text(SIGNED_PDF, CJK_TEXT) if cjk_family else 0
    print(f"[search] latin text recovered : {'yes' if latin_verified > 0 else 'no'}")
    print(f"[search] cjk text recovered   : {'yes' if cjk_verified > 0 else 'no'}")
    if not cjk_family:
        print("[warn] no CJK font in this image - install fonts-noto-cjk (see Dockerfile) to sign CJK text.")

    print(f"[result] {os.path.abspath(SIGNED_PDF)}")
    return 0 if latin_verified > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
