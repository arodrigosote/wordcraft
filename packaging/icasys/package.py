#!/usr/bin/env python3
"""ICASYS release packager (runs on the GitHub runner of each OS; only needs python 3 and cargo).

    package.py macos   [--out dist]      universal .app in a .dmg (needs both target/<triple>/release binaries)
    package.py linux   [--out dist]      .tar.gz (portable) and .deb
    package.py windows [--out dist]      portable .zip
    package.py release [--dist dist]     SHA256SUMS.txt, manifest.json and release notes for the files in dist/

Config: packaging/icasys/icasys.json (written by icasys-brand/apply-branding.py).
Nothing here is signed or notarised: the notes and the LEEME say so explicitly.
"""
import argparse, hashlib, json, os, platform, re, shutil, subprocess, sys, tarfile, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CFG = json.loads((HERE / "icasys.json").read_text(encoding="utf-8"))
APP, NAME, SLUG, UP = CFG["app"], CFG["name"], CFG["slug"], CFG["upstream_name"]
OWNER = CFG.get("owner", "arodrigosote")


def sh(*a, **k):
    return subprocess.run([str(x) for x in a], check=True, **k)


def version():
    t = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    sec = t.split("[workspace.package]", 1)[1] if "[workspace.package]" in t else t
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', sec).group(1)


def commit():
    return os.environ.get("GITHUB_SHA") or subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.strip()


VERSION = version()


def write_licenses(dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md", "ICASYS.md"):
        if (ROOT / f).is_file():
            shutil.copy(ROOT / f, dest / f)
    for base in (ROOT / "assets", Path(os.environ["CRAFT_FONTS_DIR"]) if os.environ.get("CRAFT_FONTS_DIR") else None):
        if not base or not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and (p.name.startswith("OFL") or p.name.startswith("LICENSE") or p.name == "ATTRIBUTION.md") and "target" not in p.parts:
                shutil.copy(p, dest / f"{base.name}-{p.parent.name}-{p.name}")
    meta = subprocess.run(["cargo", "metadata", "--locked", "--format-version", "1"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if meta.returncode == 0:
        m = json.loads(meta.stdout)
        ws = set(m["workspace_members"])
        rows = sorted({(p["name"], p["version"], p.get("license") or "SIN LICENCIA DECLARADA", p.get("repository") or "") for p in m["packages"] if p["id"] not in ws})
        with open(dest / "DEPENDENCIAS-TERCEROS.txt", "w", encoding="utf-8") as fh:
            fh.write("Dependencias de Rust incluidas en el build (metadatos de cargo; los textos completos están en el código de cada paquete).\n\n")
            for n, v, l, r in rows:
                fh.write(f"{n} {v}\t{l}\t{r}\n")
            fh.write(f"\n{len(rows)} paquetes.\n")


def leeme(os_name: str) -> str:
    head = f"{NAME} {VERSION} (edición ICASYS)\n\n"
    tail = (f"\nPROCEDENCIA: edición de ICASYS basada en {UP}, proyecto de código abierto del equipo de ArtCraft (https://getartcraft.com).\n"
            f"No es un producto de ArtCraft ni está respaldado por ellos. Código de esta edición: https://github.com/{OWNER}/{APP} (rama icasys, commit {commit()[:12]}).\n"
            "Licencias: carpeta Licencias (MIT o Apache-2.0 a tu elección, más avisos de terceros).\n")
    if os_name == "macos":
        return head + ("INSTALAR: arrastra la app a la carpeta Aplicaciones.\nPRIMER ARRANQUE: esta versión está firmada solo de forma local y TODAVÍA NO está notarizada por Apple. "
                       "Si macOS dice que no puede verificar al desarrollador: clic derecho sobre la app > Abrir > Abrir, o Ajustes del Sistema > Privacidad y seguridad > \"Abrir de todos modos\".\n"
                       "Comprueba el archivo descargado con:  shasum -a 256 -c SHA256SUMS.txt\n") + tail
    if os_name == "windows":
        return head + ("INSTALAR: descomprime la carpeta donde quieras y abre el .exe (versión portable, no requiere instalación).\nPRIMER ARRANQUE: el programa TODAVÍA NO está firmado, "
                       "así que Windows SmartScreen puede avisar. Pulsa \"Más información\" > \"Ejecutar de todos modos\" solo si lo descargaste del sitio oficial de ICASYS.\n"
                       "Comprueba el archivo descargado (PowerShell):  Get-FileHash <archivo> -Algorithm SHA256\n") + tail
    return head + (f"INSTALAR (.deb, Debian/Ubuntu):  sudo apt install ./icasys-{SLUG}_*.deb\nPORTABLE (.tar.gz): descomprime y ejecuta ./{APP}\n"
                   "Requiere un entorno gráfico X11 o Wayland con OpenGL/Vulkan.\nComprueba el archivo descargado con:  sha256sum -c SHA256SUMS.txt\n") + tail


def macos(out: Path):
    triples = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
    bins = {a: ROOT / "target" / t / "release" / APP for a, t in triples.items() if (ROOT / "target" / t / "release" / APP).is_file()}
    if not bins:
        sys.exit("faltan los binarios de macOS en target/<triple>/release")
    arch = "universal" if len(bins) == 2 else next(iter(bins))
    stage = out / "stage-macos"
    shutil.rmtree(stage, ignore_errors=True)
    app = stage / f"{NAME}.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir(parents=True)
    plist = (ROOT / "packaging/macos/Info.plist.in").read_text(encoding="utf-8")
    key = lambda k: re.search(rf"<key>{k}</key>\s*<string>([^<]*)</string>", plist).group(1)
    exe, icon = key("CFBundleExecutable"), key("CFBundleIconFile")
    dst = app / "Contents" / "MacOS" / exe
    if len(bins) == 2:
        sh("lipo", "-create", "-output", dst, *bins.values())
    else:
        shutil.copy(next(iter(bins.values())), dst)
    os.chmod(dst, 0o755)
    shutil.copy(ROOT / f"assets/app-icon/{APP}.icns", app / "Contents/Resources" / f"{icon}.icns")
    short = VERSION.split("-")[0]
    (app / "Contents/Info.plist").write_text(plist.replace("@VERSION@", VERSION).replace("@SHORT_VERSION@", short).replace("@BUILD_SHA@", commit()[:12]), encoding="utf-8")
    (app / "Contents/PkgInfo").write_text("APPL????")
    ident = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    ent = ROOT / "packaging/macos/entitlements.plist"
    if ident:   # Developer ID: hardened runtime + secure timestamp (required for notarisation)
        extra = ["--entitlements", str(ent)] if ent.is_file() else []
        sh("codesign", "--force", "--options", "runtime", "--timestamp", *extra, "--sign", ident, dst)
        sh("codesign", "--force", "--options", "runtime", "--timestamp", *extra, "--sign", ident, app)
        sh("codesign", "--verify", "--strict", "--deep", app)
    else:       # ad-hoc: runs on this Mac only, Gatekeeper will warn on any other
        sh("codesign", "--force", "--sign", "-", dst)
        sh("codesign", "--force", "--sign", "-", app)
    write_licenses(stage / "Licencias")
    (stage / "LEEME.txt").write_text(leeme("macos"), encoding="utf-8")
    os.symlink("/Applications", stage / "Aplicaciones")
    dmg = out / f"icasys-{SLUG}-{VERSION}-macos-{arch}.dmg"
    sh("hdiutil", "create", "-quiet", "-volname", f"{NAME} {VERSION}", "-srcfolder", stage, "-ov", "-format", "UDZO", dmg)
    shutil.rmtree(stage)
    notarized = False
    if ident:
        sh("codesign", "--force", "--timestamp", "--sign", ident, dmg)
        apple_id, pw, team = (os.environ.get(k, "") for k in ("APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID"))
        if apple_id and pw and team:
            sh("xcrun", "notarytool", "submit", dmg, "--apple-id", apple_id, "--password", pw, "--team-id", team, "--wait")
            sh("xcrun", "stapler", "staple", dmg)
            sh("xcrun", "stapler", "validate", dmg)
            notarized = True
    (out / "signing-macos.json").write_text(json.dumps({"signed": bool(ident), "notarized": notarized, "how": "Developer ID" if ident else "ad-hoc"}), encoding="utf-8")
    print(dmg)


def linux(out: Path):
    binp = ROOT / "target" / "release" / APP
    arch = {"x86_64": "x86_64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    deb_arch = {"x86_64": "amd64", "arm64": "arm64"}.get(arch, arch)
    desktop = next((ROOT / "packaging/linux").glob("*.desktop"))
    app_id = desktop.stem
    icons = ROOT / "assets/app-icon/hicolor"
    # --- portable tar.gz
    base = f"icasys-{SLUG}-{VERSION}-linux-{arch}"
    stage = out / "stage-linux" / base
    shutil.rmtree(out / "stage-linux", ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copy(binp, stage / APP)
    os.chmod(stage / APP, 0o755)
    shutil.copy(desktop, stage / desktop.name)
    shutil.copy(icons / "256x256/apps" / f"{app_id}.png", stage / f"{app_id}.png")
    write_licenses(stage / "Licencias")
    (stage / "LEEME.txt").write_text(leeme("linux"), encoding="utf-8")
    tgz = out / f"{base}.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(stage, arcname=base)
    # --- .deb
    deb = out / "stage-linux" / "deb"
    (deb / "usr/bin").mkdir(parents=True)
    shutil.copy(binp, deb / "usr/bin" / APP)
    os.chmod(deb / "usr/bin" / APP, 0o755)
    (deb / "usr/share/applications").mkdir(parents=True)
    shutil.copy(desktop, deb / "usr/share/applications" / desktop.name)
    for sz in icons.glob("*x*"):
        p = sz / "apps" / f"{app_id}.png"
        if p.is_file():
            d = deb / "usr/share/icons/hicolor" / sz.name / "apps"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, d / p.name)
    write_licenses(deb / "usr/share/doc" / f"icasys-{SLUG}" / "Licencias")
    (deb / "DEBIAN").mkdir()
    size_kb = sum(f.stat().st_size for f in deb.rglob("*") if f.is_file()) // 1024
    (deb / "DEBIAN/control").write_text(
        f"Package: icasys-{SLUG}\nVersion: {VERSION}-icasys1\nSection: graphics\nPriority: optional\nArchitecture: {deb_arch}\n"
        f"Maintainer: ICASYS <https://icasys.mx>\nInstalled-Size: {size_kb}\nDepends: libc6, libxkbcommon0, libx11-6, libgl1\n"
        f"Homepage: https://icasys.mx\nDescription: {NAME}, edición ICASYS de {UP}\n Edición de ICASYS basada en {UP} del equipo de ArtCraft.\n"
        " Licencia MIT o Apache-2.0. No es un producto de ArtCraft.\n", encoding="utf-8")
    debfile = out / f"icasys-{SLUG}_{VERSION}-icasys1_{deb_arch}.deb"
    sh("dpkg-deb", "--root-owner-group", "--build", deb, debfile)
    shutil.rmtree(out / "stage-linux")
    print(tgz, debfile)


def windows(out: Path):
    exe = ROOT / "target" / "release" / f"{APP}.exe"
    base = f"icasys-{SLUG}-{VERSION}-windows-x64"
    stage = out / "stage-windows" / NAME
    shutil.rmtree(out / "stage-windows", ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copy(exe, stage / f"{NAME}.exe")
    signed = False
    pfx, pw = os.environ.get("WIN_SIGN_PFX", ""), os.environ.get("WIN_SIGN_PASSWORD", "")
    if pfx and Path(pfx).is_file():
        tools = sorted(Path("C:/Program Files (x86)/Windows Kits/10/bin").glob("*/x64/signtool.exe"))
        if not tools:
            sys.exit("signtool.exe no encontrado")
        sh(tools[-1], "sign", "/f", pfx, "/p", pw, "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256", stage / f"{NAME}.exe")
        signed = True
    (out / "signing-windows.json").write_text(json.dumps({"signed": signed, "notarized": False, "how": "Authenticode" if signed else "sin firma"}), encoding="utf-8")
    write_licenses(stage / "Licencias")
    (stage / "LEEME.txt").write_text(leeme("windows"), encoding="utf-8")
    z = out / f"{base}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(stage.parent))
    shutil.rmtree(out / "stage-windows")
    print(z)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def release(dist: Path):
    kinds = {".dmg": "macos", ".zip": "windows", ".deb": "linux", ".gz": "linux"}
    files = sorted(p for p in dist.iterdir() if p.is_file() and p.suffix in kinds)
    if not files:
        sys.exit("no hay paquetes en dist/")
    sign = {}
    for f in dist.glob("signing-*.json"):
        sign[f.stem.split("-", 1)[1]] = json.loads(f.read_text(encoding="utf-8"))
    sign.setdefault("linux", {"signed": False, "notarized": False, "how": "sin firma de paquete"})
    if (dist / "SHA256SUMS.txt.asc").exists() or (dist / "ICASYS-firma-publica.asc").exists():
        sign["linux"] = {"signed": True, "notarized": False, "how": "suma firmada con GPG"}
    assets = []
    for p in files:
        n = p.name
        arch = "universal" if "universal" in n else "arm64" if ("arm64" in n) else "x64" if ("x64" in n or "x86_64" in n or "amd64" in n) else "?"
        assets.append({"file": n, "os": kinds[p.suffix], "arch": arch, "kind": "deb" if p.suffix == ".deb" else "tar.gz" if p.suffix == ".gz" else p.suffix[1:], "size_bytes": p.stat().st_size, "sha256": sha256(p), "signing": sign.get(kinds[p.suffix], {})})
    (dist / "SHA256SUMS.txt").write_text("".join(f"{a['sha256']}  {a['file']}\n" for a in assets), encoding="utf-8")
    c = commit()
    (dist / "manifest.json").write_text(json.dumps({
        "name": NAME, "app_id": APP, "version": VERSION, "edition": "icasys", "commit": c, "branch": "icasys",
        "upstream_base": CFG.get("upstream_base", ""), "signing": sign, "assets": assets,
        "source": f"https://github.com/{OWNER}/{APP}/tree/{c}"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    mb = lambda n: f"{n / 1048576:.0f} MB"
    rows = "\n".join(f"| `{a['file']}` | {a['os']} | {a['arch']} | {mb(a['size_bytes'])} | {a['signing'].get('how', '?')}{' + notarizado' if a['signing'].get('notarized') else ''} |" for a in assets)
    unsigned = [o for o, v in sign.items() if o in {a['os'] for a in assets} and not v.get("signed")]
    warn = ("\n## ⚠️ Vista previa: sin firma de distribución en " + ", ".join(unsigned) + "\n\nEstos paquetes no están firmados"
            + (" ni notarizados" if "macos" in unsigned else "") + f". {'macOS' if 'macos' in unsigned else 'El sistema'} o Windows pueden mostrar una advertencia la primera vez; el `LEEME` de cada paquete explica cómo abrirlos. "
            "Descárgalos solo de esta página o del sitio oficial de ICASYS y comprueba la suma SHA-256.\n") if unsigned else ""
    (dist / "title.txt").write_text(f"{NAME} {VERSION} · edición ICASYS 1" + (" (vista previa)" if unsigned else ""), encoding="utf-8")
    (dist / "RELEASE_NOTES.md").write_text(f"""# {NAME} {VERSION} — edición ICASYS 1

Edición de **ICASYS** (<https://icasys.mx>) del programa de código abierto **{UP}**, creado por el equipo y la comunidad de **ArtCraft** (<https://getartcraft.com>). No es un producto de ArtCraft ni cuenta con su respaldo.

## Descargas

| Archivo | Sistema | Arquitectura | Tamaño | Firma |
|---|---|---|---|---|
{rows}

Las sumas SHA-256 están en `SHA256SUMS.txt`. Cada paquete trae un `LEEME` con las instrucciones de instalación.

{warn}
## Qué cambia respecto a {UP} {VERSION}

Solo la identidad visible: nombre del producto, iconos, enlaces de ayuda y "Acerca de" hacia ICASYS, y el crédito de atribución. El motor no se modifica. Interfaz en español incluida (excepto donde el proyecto original no la ofrece).

## Privacidad

Sin telemetría, sin cuentas y sin servicios en la nube. Las conexiones de red son opcionales y las inicia el usuario.

## Código fuente y licencias

- Código de esta edición: commit `{c[:12]}` de la rama `icasys`.
- Licencia: **MIT o Apache-2.0, a elección del usuario**. Los textos, los avisos de terceros y la lista de dependencias están en la carpeta `Licencias` de cada paquete.
- El nombre y los logos de ArtCraft son marcas de ArtCraft Team y no se usan en esta edición. El nombre y el isotipo de ICASYS son marcas de ICASYS.
""", encoding="utf-8")
    print(f"{len(assets)} paquetes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["macos", "linux", "windows", "release"])
    ap.add_argument("--out", default="dist")
    ap.add_argument("--dist", default="dist")
    a = ap.parse_args()
    if a.cmd == "release":
        release(Path(a.dist))
    else:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        {"macos": macos, "linux": linux, "windows": windows}[a.cmd](out.resolve())
