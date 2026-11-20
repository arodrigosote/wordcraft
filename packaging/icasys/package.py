#!/usr/bin/env python3
"""ICASYS release packager (runs on the CI runner of each OS, or locally; only needs python 3 and cargo).

    package.py macos        [--out dist]   universal .app in a .dmg (needs both target/<triple>/release binaries)
    package.py linux        [--out dist]   .deb (and a portable .tar.gz when ICASYS_PORTABLE=1)
    package.py windows      [--out dist]   .msi built with WiX (wix + WixToolset.UI.wixext on PATH)
    package.py windows-test [--out dist]   installs the .msi for real, checks it and uninstalls it
    package.py release      [--dist dist]  SHA256SUMS.txt, manifest.json and release notes for the files in dist/

Config: packaging/icasys/icasys.json (written by icasys-brand/apply-branding.py from icasys-brand/apps.json).
Signing is optional: without the signing secrets the packages are unsigned and the notes and the LEEME say so.
"""
import argparse, hashlib, json, os, platform, re, shutil, subprocess, sys, tarfile, time, uuid, zipfile
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CFG = json.loads((HERE / "icasys.json").read_text(encoding="utf-8"))
APP, NAME, SLUG, UP = CFG["app"], CFG["name"], CFG["slug"], CFG["upstream_name"]
CLI = CFG.get("cli")   # companion command-line binary that also serves MCP (only for apps that ship it)
CLI_PROJECT_EXT = CFG.get("cli_project_ext")   # e.g. "fcproj": shown in the MCP example, if the app has a project format
ES_UI = CFG.get("es_ui", True)                 # False when upstream ships no Spanish translation yet
DEB_SECTION = CFG.get("deb_section", "graphics")
OWNER = CFG.get("owner", "arodrigosote")


def edition():
    """ICASYS edition number: the N of tag icasys-v<version>-N (1 when not building a tag)."""
    m = re.search(r"-(\d+)$", os.environ.get("GITHUB_REF_NAME", "") or os.environ.get("ICASYS_TAG", ""))
    return int(m.group(1)) if m else 1


def ci_error(title: str, text: str):
    """On GitHub Actions, an error annotation: the public API returns annotations without signing in, unlike logs."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        esc = lambda s: s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={esc(title).replace(',', ' ').replace(':', ' ')}::{esc(text[-3500:])}", flush=True)


def sh(*a, **k):
    """Runs a command; on failure its last output lines go to stderr and to a CI annotation."""
    args = [str(x) for x in a]
    if not os.path.isabs(args[0]):
        # On Windows CreateProcess searches the current folder and PATH for a bare name and can hit something that is
        # not the program ("Access is denied" for `wix` on the GitHub runner): resolve it like a shell does.
        found = shutil.which(args[0])
        if found:
            args[0] = found
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **k)
    if r.stdout:
        print(r.stdout, end="", flush=True)
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr, flush=True)
    if r.returncode != 0:
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-25:])
        ci_error(f"{Path(args[0]).name} falló ({r.returncode})", " ".join(args)[:400] + "\n" + tail)
        raise subprocess.CalledProcessError(r.returncode, args)
    return r


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
    for base in (ROOT / "assets", Path(os.environ["CRAFT_FONTS_DIR"]) if os.environ.get("CRAFT_FONTS_DIR") else None,
                 HERE / "craft-i18n"):   # translations embedded in the binary (apps that use craft-i18n)
        if not base or not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and (p.name.startswith("OFL") or p.name.startswith("LICENSE") or p.name == "ATTRIBUTION.md") and "target" not in p.parts:
                shutil.copy(p, dest / f"{base.name}-{p.parent.name}-{p.name}")
    lang = HERE / "craft-i18n" / "language-data"   # dictionaries/hyphenation embedded by craft-proof: their notices
    if lang.is_dir():
        if (lang / "README.md").is_file():
            shutil.copy(lang / "README.md", dest / "language-data-README.md")
        for p in sorted((lang / "licences").glob("*")) if (lang / "licences").is_dir() else []:
            if p.is_file():
                shutil.copy(p, dest / f"language-data-licences-{p.name}")
    try:
        meta = subprocess.run(["cargo", "metadata", "--locked", "--format-version", "1"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        sys.exit("falta cargo en el PATH: hace falta para la lista de dependencias (DEPENDENCIAS-TERCEROS.txt)")
    if meta.returncode == 0:
        m = json.loads(meta.stdout)
        ws = set(m["workspace_members"])
        rows = sorted({(p["name"], p["version"], p.get("license") or "SIN LICENCIA DECLARADA", p.get("repository") or "") for p in m["packages"] if p["id"] not in ws})
        with open(dest / "DEPENDENCIAS-TERCEROS.txt", "w", encoding="utf-8") as fh:
            fh.write("Dependencias de Rust incluidas en el build (metadatos de cargo; los textos completos están en el código de cada paquete).\n\n")
            for n, v, l, r in rows:
                fh.write(f"{n} {v}\t{l}\t{r}\n")
            fh.write(f"\n{len(rows)} paquetes.\n")


def mcp_paths():
    return {"macos": f"/Applications/{NAME}.app/Contents/MacOS/{CLI}", "windows": f"C:\\Program Files\\ICASYS\\{NAME}\\{CLI}.exe", "linux": f"/usr/bin/{CLI}"}


def mcp_leeme(os_name: str) -> str:
    if not CLI:
        return ""
    path = mcp_paths()[os_name]
    return (f"\nCONECTAR UNA IA (MCP): este paquete incluye {CLI}, que sirve el protocolo MCP. NO está activado por defecto: solo funciona cuando un cliente de IA lo lanza.\n"
            f"  Sin ventana:   \"{path}\" mcp --demo\n"
            f"  Con la app abierta (puente): abre la app con --control 9876 y usa:  \"{path}\" mcp --bridge 127.0.0.1:9876\n"
            f"  Ejemplo para Claude Code:  claude mcp add icasys-{SLUG} -- <ruta> mcp --demo\n"
            "SEGURIDAD: el puerto --control no tiene autenticación (solo escucha en 127.0.0.1, pero cualquier programa de ese equipo puede usarlo mientras esté abierto) y el servidor MCP "
            "puede leer y escribir donde el usuario pueda. Úsalo solo con clientes de IA de confianza y no lo dejes activo en equipos compartidos. Si la IA es un servicio en la nube, recibe los fotogramas y pantallas que pida.\n")


def _leeme_base(os_name: str) -> str:
    head = f"{NAME} {VERSION} (edición ICASYS)\n\n"
    tail = (f"\nPROCEDENCIA: edición de ICASYS basada en {UP}, proyecto de código abierto del equipo de ArtCraft (https://getartcraft.com).\n"
            f"No es un producto de ArtCraft ni está respaldado por ellos. Código de esta edición: https://github.com/{OWNER}/{APP} (rama icasys, commit {commit()[:12]}).\n"
            "Licencias: carpeta Licencias (MIT o Apache-2.0 a tu elección, más avisos de terceros).\n")
    if os_name == "macos":
        return head + ("INSTALAR: arrastra la app a la carpeta Aplicaciones.\nPRIMER ARRANQUE: esta versión está firmada solo de forma local y TODAVÍA NO está notarizada por Apple. "
                       "Si macOS dice que no puede verificar al desarrollador: clic derecho sobre la app > Abrir > Abrir, o Ajustes del Sistema > Privacidad y seguridad > \"Abrir de todos modos\".\n"
                       "Comprueba el archivo descargado con:  shasum -a 256 -c SHA256SUMS.txt\n") + tail
    if os_name == "windows":
        return head + ("INSTALAR: abre el archivo .msi y sigue el asistente (pide permiso de administrador). Se instala en Archivos de programa, "
                       "crea el acceso en el menú Inicio y se desinstala desde Configuración > Aplicaciones.\n"
                       "INSTALACIÓN SILENCIOSA (administradores):  msiexec /i <archivo>.msi /qn\n"
                       "PRIMER ARRANQUE: el instalador TODAVÍA NO está firmado, así que Windows SmartScreen puede avisar. Pulsa \"Más información\" > "
                       "\"Ejecutar de todos modos\" solo si lo descargaste del sitio oficial de ICASYS.\n"
                       "Comprueba el archivo descargado (PowerShell):  Get-FileHash <archivo> -Algorithm SHA256\n") + tail
    return head + (f"INSTALAR (.deb, Debian/Ubuntu):  sudo apt install ./icasys-{SLUG}_*.deb\nDESINSTALAR:  sudo apt remove icasys-{SLUG}\n"
                   "Requiere un entorno gráfico X11 o Wayland con OpenGL/Vulkan.\nComprueba el archivo descargado con:  sha256sum -c SHA256SUMS.txt\n") + tail


def leeme(os_name: str) -> str:
    return _leeme_base(os_name) + mcp_leeme(os_name)


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
    cli_dst = None
    if CLI:
        cbins = {a: ROOT / "target" / t / "release" / CLI for a, t in triples.items() if (ROOT / "target" / t / "release" / CLI).is_file()}
        if set(cbins) != set(bins):
            sys.exit(f"{CLI} no se compiló para las mismas arquitecturas que {APP}: {sorted(cbins)} vs {sorted(bins)}")
        cli_dst = app / "Contents" / "MacOS" / CLI
        if len(cbins) == 2:
            sh("lipo", "-create", "-output", cli_dst, *cbins.values())
        else:
            shutil.copy(next(iter(cbins.values())), cli_dst)
        os.chmod(cli_dst, 0o755)
    shutil.copy(ROOT / f"assets/app-icon/{APP}.icns", app / "Contents/Resources" / f"{icon}.icns")
    short = VERSION.split("-")[0]
    (app / "Contents/Info.plist").write_text(plist.replace("@VERSION@", VERSION).replace("@SHORT_VERSION@", short).replace("@BUILD_SHA@", commit()[:12]), encoding="utf-8")
    (app / "Contents/PkgInfo").write_text("APPL????")
    ident = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    ent = ROOT / "packaging/macos/entitlements.plist"
    if ident:   # Developer ID: hardened runtime + secure timestamp (required for notarisation)
        extra = ["--entitlements", str(ent)] if ent.is_file() else []
        for f in (cli_dst, dst):   # nested code first, then the main executable, then the bundle
            if f:
                sh("codesign", "--force", "--options", "runtime", "--timestamp", *extra, "--sign", ident, f)
        sh("codesign", "--force", "--options", "runtime", "--timestamp", *extra, "--sign", ident, app)
        sh("codesign", "--verify", "--strict", "--deep", app)
    else:       # ad-hoc: runs on this Mac only, Gatekeeper will warn on any other
        for f in (cli_dst, dst):   # nested code first, then the main executable, then the bundle
            if f:
                sh("codesign", "--force", "--sign", "-", f)
        sh("codesign", "--force", "--sign", "-", app)
    write_licenses(stage / "Licencias")
    (stage / "LEEME.txt").write_text(leeme("macos"), encoding="utf-8")
    os.symlink("/Applications", stage / "Aplicaciones")
    dmg = out / f"icasys-{SLUG}-{VERSION}-macos-{arch}.dmg"
    for attempt in range(1, 5):   # hdiutil on CI runners sometimes fails with "Resource busy": retry, and show why if it keeps failing
        r = subprocess.run(["hdiutil", "create", "-volname", f"{NAME} {VERSION}", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(dmg)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 0:
            break
        print(f"hdiutil intento {attempt} falló ({r.returncode}): {r.stdout.strip()} {r.stderr.strip()}", file=sys.stderr)
        time.sleep(20)
    else:
        sys.exit("hdiutil create falló 4 veces")
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
    PORTABLE = os.environ.get("ICASYS_PORTABLE") == "1"   # portable archives are no longer published by default
    base = f"icasys-{SLUG}-{VERSION}-linux-{arch}"
    stage = out / "stage-linux" / base
    shutil.rmtree(out / "stage-linux", ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copy(binp, stage / APP)
    os.chmod(stage / APP, 0o755)
    if CLI:
        shutil.copy(ROOT / "target" / "release" / CLI, stage / CLI)
        os.chmod(stage / CLI, 0o755)
    shutil.copy(desktop, stage / desktop.name)
    shutil.copy(icons / "256x256/apps" / f"{app_id}.png", stage / f"{app_id}.png")
    write_licenses(stage / "Licencias")
    (stage / "LEEME.txt").write_text(leeme("linux"), encoding="utf-8")
    tgz = out / f"{base}.tar.gz"
    if PORTABLE:
        with tarfile.open(tgz, "w:gz") as t:
            t.add(stage, arcname=base)
    # --- .deb
    deb = out / "stage-linux" / "deb"
    (deb / "usr/bin").mkdir(parents=True)
    shutil.copy(binp, deb / "usr/bin" / APP)
    os.chmod(deb / "usr/bin" / APP, 0o755)
    if CLI:
        shutil.copy(ROOT / "target" / "release" / CLI, deb / "usr/bin" / CLI)
        os.chmod(deb / "usr/bin" / CLI, 0o755)
    (deb / "usr/share/applications").mkdir(parents=True)
    shutil.copy(desktop, deb / "usr/share/applications" / desktop.name)
    for sz in icons.glob("*x*"):
        p = sz / "apps" / f"{app_id}.png"
        if p.is_file():
            d = deb / "usr/share/icons/hicolor" / sz.name / "apps"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, d / p.name)
    write_licenses(deb / "usr/share/doc" / f"icasys-{SLUG}" / "Licencias")
    (deb / "usr/share/doc" / f"icasys-{SLUG}" / "LEEME.txt").write_text(leeme("linux"), encoding="utf-8")
    (deb / "DEBIAN").mkdir()
    size_kb = sum(f.stat().st_size for f in deb.rglob("*") if f.is_file()) // 1024
    (deb / "DEBIAN/control").write_text(
        f"Package: icasys-{SLUG}\nVersion: {VERSION}-icasys{edition()}\nSection: {DEB_SECTION}\nPriority: optional\nArchitecture: {deb_arch}\n"
        f"Maintainer: ICASYS <https://icasys.mx>\nInstalled-Size: {size_kb}\nDepends: libc6, libxkbcommon0, libx11-6, libgl1\n"
        f"Homepage: https://icasys.mx\nDescription: {NAME}, edición ICASYS de {UP}\n Edición de ICASYS basada en {UP} del equipo de ArtCraft.\n"
        " Licencia MIT o Apache-2.0. No es un producto de ArtCraft.\n", encoding="utf-8")
    debfile = out / f"icasys-{SLUG}_{VERSION}-icasys{edition()}_{deb_arch}.deb"
    # xz, not Ubuntu's default zstd: installs on every Debian/Ubuntu release and every tool can read it
    sh("dpkg-deb", "-Zxz", "--root-owner-group", "--build", deb, debfile)
    shutil.rmtree(out / "stage-linux")
    print(debfile)


def rtf(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\par\n")
        elif ord(ch) > 127:
            out.append("\\u%d?" % (ord(ch) if ord(ch) < 32768 else ord(ch) - 65536))
        elif ch != "\r":
            out.append(ch)
    return "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\fs18 " + "".join(out) + "}"


def windows(out: Path):
    exe = ROOT / "target" / "release" / f"{APP}.exe"
    stage = out / "stage-windows"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "app").mkdir(parents=True)
    shutil.copy(exe, stage / "app" / f"{NAME}.exe")
    if CLI:
        shutil.copy(ROOT / "target" / "release" / f"{CLI}.exe", stage / "app" / f"{CLI}.exe")
    signed = False
    pfx, pw = os.environ.get("WIN_SIGN_PFX", ""), os.environ.get("WIN_SIGN_PASSWORD", "")
    signtool = None
    if pfx and Path(pfx).is_file():
        tools = sorted(Path("C:/Program Files (x86)/Windows Kits/10/bin").glob("*/x64/signtool.exe"))
        if not tools:
            sys.exit("signtool.exe no encontrado")
        signtool = tools[-1]
        for f in [stage / "app" / f"{NAME}.exe"] + ([stage / "app" / f"{CLI}.exe"] if CLI else []):
            sh(signtool, "sign", "/f", pfx, "/p", pw, "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256", f)
        signed = True
    write_licenses(stage / "app" / "Licencias")
    (stage / "app" / "LEEME.txt").write_text(leeme("windows"), encoding="utf-8")
    # --- license page of the installer
    mit = (ROOT / "LICENSE-MIT").read_text(encoding="utf-8") if (ROOT / "LICENSE-MIT").is_file() else ""
    lic = (f"{NAME} {VERSION} (edición ICASYS)\n\nBasado en {UP}, del equipo de ArtCraft (https://getartcraft.com). No es un producto de ArtCraft.\n"
           "Licencia: MIT o Apache-2.0, a elección del usuario. Los textos completos y los avisos de terceros están en la carpeta Licencias de la instalación.\n\n" + mit)
    (stage / "license.rtf").write_text(rtf(lic), encoding="ascii")
    # --- WiX source: one component per file (Guid="*"), the licence files in a Licencias subfolder
    msi_ver = re.match(r"\d+\.\d+\.\d+", VERSION).group(0)
    upgrade = str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://icasys.mx/{APP}")).upper()
    ico = (ROOT / f"assets/app-icon/{APP}.ico").as_posix()
    comps, lic_comps, n = [], [], 0
    for p in sorted((stage / "app").rglob("*")):
        if not p.is_file():
            continue
        n += 1
        rel = p.relative_to(stage / "app")
        nm = xml_escape(p.name, {'"': "&quot;"})
        c = f'<Component Id="c{n}" Guid="*"><File Id="f{n}" Source="{p.as_posix()}" Name="{nm}" KeyPath="yes" />{"" if len(rel.parts) == 1 else ""}</Component>'
        (lic_comps if len(rel.parts) > 1 else comps).append(c)
    name_x = xml_escape(NAME, {'"': "&quot;"})
    wxs = f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs" xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui">
  <Package Name="{name_x}" Manufacturer="ICASYS" Version="{msi_ver}" UpgradeCode="{upgrade}" Scope="perMachine" Language="1034" Compressed="yes">
    <SummaryInformation Description="{name_x} (edición ICASYS de {xml_escape(UP)})" />
    <MajorUpgrade AllowSameVersionUpgrades="yes" DowngradeErrorMessage="Ya hay una versión más reciente de {name_x} instalada." />
    <MediaTemplate EmbedCab="yes" />
    <Icon Id="AppIcon.ico" SourceFile="{ico}" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon.ico" />
    <Property Id="ARPURLINFOABOUT" Value="https://icasys.mx" />
    <Property Id="ARPHELPLINK" Value="https://icasys.mx" />
    <WixVariable Id="WixUILicenseRtf" Value="{(stage / 'license.rtf').as_posix()}" />
    <ui:WixUI Id="WixUI_InstallDir" InstallDirectory="INSTALLFOLDER" />
    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="ICASYSDIR" Name="ICASYS">
        <Directory Id="INSTALLFOLDER" Name="{name_x}">
          <Directory Id="LICDIR" Name="Licencias" />
        </Directory>
      </Directory>
    </StandardDirectory>
    <StandardDirectory Id="ProgramMenuFolder" />
    <ComponentGroup Id="AppFiles" Directory="INSTALLFOLDER">{"".join(comps)}</ComponentGroup>
    <ComponentGroup Id="LicFiles" Directory="LICDIR">{"".join(lic_comps)}</ComponentGroup>
    <Component Id="MenuShortcut" Directory="ProgramMenuFolder" Guid="*">
      <Shortcut Id="StartMenuShortcut" Name="{name_x}" Target="[INSTALLFOLDER]{name_x}.exe" WorkingDirectory="INSTALLFOLDER" Icon="AppIcon.ico" />
      <RegistryValue Root="HKCU" Key="Software\\ICASYS\\{APP}" Name="installed" Type="integer" Value="1" KeyPath="yes" />
    </Component>
    <Feature Id="Main" Title="{name_x}" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentGroupRef Id="LicFiles" />
      <ComponentRef Id="MenuShortcut" />
    </Feature>
  </Package>
</Wix>
"""
    (stage / "product.wxs").write_text(wxs, encoding="utf-8")
    msi = out / f"icasys-{SLUG}-{VERSION}-windows-x64.msi"
    # WIX: path to a specific wix.exe (local builds keep WiX 5.0.2 apart from any other WiX); CI puts wix on PATH
    sh(os.environ.get("WIX", "wix"), "build", "-arch", "x64", "-ext", "WixToolset.UI.wixext", "-o", msi, stage / "product.wxs")
    msi.with_suffix(".wixpdb").unlink(missing_ok=True)   # WiX debug symbols: not a deliverable
    if signtool:
        sh(signtool, "sign", "/f", pfx, "/p", pw, "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256", msi)
    (out / "signing-windows.json").write_text(json.dumps({"signed": signed, "notarized": False, "how": "Authenticode" if signed else "sin firma"}), encoding="utf-8")
    shutil.rmtree(stage)
    print(msi)


def windows_test(out: Path):
    """Installs the MSI for real on the runner, checks files and the Start-menu shortcut, uninstalls and checks it is gone."""
    msis = sorted(out.glob("*.msi"))
    if not msis:
        sys.exit("no hay .msi en dist/")
    msi = msis[0]
    inst = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ICASYS" / NAME
    lnk = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs" / f"{NAME}.lnk"
    log = out / "msi-test.log"
    r = subprocess.run(["msiexec", "/i", str(msi), "/qn", "/norestart", "/l*v", str(log)])
    if r.returncode != 0:
        print(log.read_text(errors="replace")[-3000:]); sys.exit(f"msiexec /i falló ({r.returncode})")
    problems = [str(p) for p in [inst / f"{NAME}.exe", inst / "LEEME.txt", inst / "Licencias" / "LICENSE-MIT", inst / "Licencias" / "NOTICE", lnk] + ([inst / f"{CLI}.exe"] if CLI else []) if not p.exists()]
    r2 = subprocess.run(["msiexec", "/x", str(msi), "/qn", "/norestart", "/l*v", str(out / "msi-uninstall.log")])
    left = [str(p) for p in (inst / f"{NAME}.exe", lnk) if p.exists()]
    for f in (log, out / "msi-uninstall.log"):
        f.unlink(missing_ok=True)
    if problems:
        sys.exit("Instalación incompleta, faltan: " + ", ".join(problems))
    if r2.returncode != 0 or left:
        sys.exit(f"La desinstalación no limpió ({r2.returncode}): {left}")
    print(f"OK: {msi.name} se instala (exe, licencias, acceso del menú Inicio) y se desinstala limpiamente")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def release(dist: Path):
    kinds = {".dmg": "macos", ".msi": "windows", ".zip": "windows", ".deb": "linux", ".gz": "linux"}
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
        "upstream_base": CFG.get("upstream_base", ""), "signing": sign, "mcp_cli": CLI, "assets": assets,
        "source": f"https://github.com/{OWNER}/{APP}/tree/{c}"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    mb = lambda n: f"{n / 1048576:.0f} MB"
    osn = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}
    archn = {"universal": "Apple Silicon e Intel", "arm64": "Apple Silicon / ARM64", "x64": "Intel/AMD 64 bits (x64)"}
    rows = "\n".join(f"| `{a['file']}` | {osn.get(a['os'], a['os'])} | {archn.get(a['arch'], a['arch'])} | {mb(a['size_bytes'])} | {a['signing'].get('how', '?')}{' + notarizado' if a['signing'].get('notarized') else ''} |" for a in assets)
    unsigned = [o for o, v in sign.items() if o in {a['os'] for a in assets} and not v.get("signed")]
    warn = ("\n## ⚠️ Vista previa: sin firma de distribución en " + ", ".join(osn.get(o, o) for o in unsigned) + "\n\nEstos paquetes no están firmados"
            + (" ni notarizados" if "macos" in unsigned else "") + f". {'macOS' if 'macos' in unsigned else 'El sistema'} o Windows pueden mostrar una advertencia la primera vez; el `LEEME` de cada paquete explica cómo abrirlos. "
            "Descárgalos solo de esta página o del sitio oficial de ICASYS y comprueba la suma SHA-256.\n") if unsigned else ""
    (dist / "title.txt").write_text(f"{NAME} {VERSION} · edición ICASYS {edition()}" + (" (vista previa)" if unsigned else ""), encoding="utf-8")
    es_note = ("Interfaz en español incluida (excepto donde el proyecto original no la ofrece)." if ES_UI else
               "**La interfaz está disponible solo en inglés por ahora**: el proyecto original todavía no incluye traducciones.")
    project_hint = f" (o `--project proyecto.{CLI_PROJECT_EXT}`)" if CLI_PROJECT_EXT else ""
    mcp_notes = (f"""## Conectar una IA (MCP)

Incluye `{CLI}`, que sirve el protocolo **MCP** para que un cliente de IA edite el proyecto. **Viene desactivado**: solo funciona cuando un cliente de IA lo lanza.

| Sistema | Ruta de `{CLI}` |
|---|---|
| macOS | `{mcp_paths()['macos']}` |
| Windows | `{mcp_paths()['windows']}` |
| Linux | `{mcp_paths()['linux']}` |

- Sin ventana: `{CLI} mcp --demo`{project_hint}.
- Con la app abierta: inicia la app con `--control 9876` y usa `{CLI} mcp --bridge 127.0.0.1:9876`.

**Seguridad:** el puerto `--control` no tiene autenticación (solo escucha en `127.0.0.1`, pero cualquier programa de ese equipo puede usarlo mientras esté abierto) y el servidor MCP puede leer y escribir donde el usuario pueda. Úsalo solo con clientes de IA de confianza y no lo dejes activo en equipos compartidos. Si la IA es un servicio en la nube, recibe los fotogramas y pantallas que pida.

""" if CLI else "")
    (dist / "RELEASE_NOTES.md").write_text(f"""# {NAME} {VERSION} — edición ICASYS {edition()}

Edición de **ICASYS** (<https://icasys.mx>) del programa de código abierto **{UP}**, creado por el equipo y la comunidad de **ArtCraft** (<https://getartcraft.com>). No es un producto de ArtCraft ni cuenta con su respaldo.

## Descargas

| Archivo | Sistema | Arquitectura | Tamaño | Firma |
|---|---|---|---|---|
{rows}

Las sumas SHA-256 están en `SHA256SUMS.txt`. Cada paquete trae un `LEEME` con las instrucciones de instalación.

{warn}
## Qué cambia respecto a {UP} {VERSION}

Solo la identidad visible: nombre del producto, iconos, enlaces de ayuda y "Acerca de" hacia ICASYS, y el crédito de atribución. El motor no se modifica. {es_note}

{mcp_notes}## Privacidad

Sin telemetría, sin cuentas y sin servicios en la nube. Las conexiones de red son opcionales y las inicia el usuario. La búsqueda de actualizaciones del proyecto original está desactivada: la app no consulta servidores al arrancar ni se actualiza sola; las versiones nuevas las distribuye ICASYS.

## Código fuente y licencias

- Código de esta edición: commit `{c[:12]}` de la rama `icasys`.
- Licencia: **MIT o Apache-2.0, a elección del usuario**. Los textos, los avisos de terceros y la lista de dependencias están en la carpeta `Licencias` de cada paquete.
- El nombre y los logos de ArtCraft son marcas de ArtCraft Team y no se usan en esta edición. El nombre y el isotipo de ICASYS son marcas de ICASYS.
""", encoding="utf-8")
    print(f"{len(assets)} paquetes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["macos", "linux", "windows", "windows-test", "release"])
    ap.add_argument("--out", default="dist")
    ap.add_argument("--dist", default="dist")
    a = ap.parse_args()
    try:
        if a.cmd == "release":
            release(Path(a.dist))
        else:
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            {"macos": macos, "linux": linux, "windows": windows, "windows-test": windows_test}[a.cmd](out.resolve())
    except subprocess.CalledProcessError:
        raise   # sh() already reported it
    except BaseException as e:   # SystemExit included: every early stop leaves a readable annotation
        if not (isinstance(e, SystemExit) and e.code in (0, None)):
            import traceback
            ci_error(f"package.py {a.cmd}", "".join(traceback.format_exception(e))[-3000:])
        raise
