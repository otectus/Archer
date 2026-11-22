#!/usr/bin/env python3
"""Assemble verified packages for one reviewed target transaction."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def include_archer_source(package, directory, stage):
    build = subprocess.run(["bsdtar", "-xOf", str(package), ".BUILDINFO"], capture_output=True, text=True, check=True)
    fields = dict(line.split(" = ", 1) for line in build.stdout.splitlines() if " = " in line)
    base, digest = fields["pkgbase"], fields["pkgbuild_sha256sum"]
    if not re.fullmatch(r"[a-z0-9.+_-]{1,80}", base) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid Archer build metadata")
    candidates = (directory, *(p for p in directory.iterdir() if p.is_dir()))
    for folder in candidates:
        recipe = folder / "PKGBUILD"
        if not recipe.is_file() or file_digest(recipe) != digest:
            continue
        srcinfo = (folder / ".SRCINFO").read_text()
        sources = re.findall(r"^\s*source = ([A-Za-z0-9_.+-]+\.tar\.gz)$", srcinfo, re.M)
        checks = re.findall(r"^\s*sha256sums = ([a-f0-9]{64})$", srcinfo, re.M)
        if len(sources) != 1 or len(checks) != 1 or f"sha256sums=('{checks[0]}')" not in recipe.read_text():
            raise ValueError("Source metadata disagrees with signed package recipe")
        target = stage / "source" / base
        if target.exists():
            if file_digest(target / "PKGBUILD") != digest:
                raise ValueError("Conflicting package recipes for the same source")
            return
        target.mkdir(parents=True)
        for name in ("PKGBUILD", ".SRCINFO", "SOURCE_DATE_EPOCH", sources[0]):
            shutil.copyfile(folder / name, target / name)
        if file_digest(target / sources[0]) != checks[0]:
            raise ValueError("Archer source differs from the packaged checksum")
        return
    raise ValueError(f"Matching recipe and source required for {base}")


def assemble(plan, cache, keyring, output, sources):
    """The plan is pacman's resolved target transaction, including the distribution upgrade."""
    rows = []
    for line in plan.read_text().splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError("Expected package name, version and package URL separated by tabs")
        name, version, location = fields
        filename = location.rsplit("/", 1)[-1]
        if not filename.endswith(".pkg.tar.zst") or not name or not version or not filename.startswith(name + "-"):
            raise ValueError("Unrecognized package in transaction plan")
        rows.append((name, version, filename))
    if not rows or len({r[0] for r in rows}) != len(rows) or not any(r[0] == "archer-core" for r in rows):
        raise ValueError("Provide a unique transaction containing archer-core")
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        stage = Path(work)
        manifest = []
        for name, version, filename in rows:
            package = cache / filename
            sig = cache / (filename + ".sig")
            dest = stage / filename
            shutil.copyfile(package, dest)
            signature = stage / sig.name
            shutil.copyfile(sig, signature)
            subprocess.run(["gpgv", "--keyring", str(keyring.resolve()), str(signature), str(dest)], check=True)
            result = subprocess.run(["pacman", "-Qp", "--", str(dest)], capture_output=True, text=True, check=True)
            if result.stdout.split() != [name, version]:
                raise ValueError(f"Package metadata changed for {filename}")
            if name.startswith("archer-"):
                include_archer_source(dest, sources, stage)
            manifest.append({"package": name, "version": version, "file": filename, "sha256": file_digest(dest)})
        (stage / "transaction.json").write_text(json.dumps({"schema_version": 1, "packages": manifest}, indent=2))
        with output.open("xb") as file:
            try:
                with tarfile.open(fileobj=file, mode="w") as archive:
                    for entry in sorted(stage.rglob("*")):
                        archive.add(entry, arcname=str(entry.relative_to(stage)), recursive=False)
            except BaseException:
                output.unlink(missing_ok=True)
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Target pacman transaction printed with %%n, %%v, %%l separated by tabs")
    parser.add_argument("cache", type=Path, help="Reviewed distribution and Archer packages and detached signatures")
    parser.add_argument("keyring", type=Path, help="Keyring containing the approved distribution and Archer signers")
    parser.add_argument("output", type=Path, help="New .tar archive")
    parser.add_argument("--sources", type=Path, required=True, help="Prepared Archer source directory or release directory")
    args = parser.parse_args()
    try:
        assemble(args.plan, args.cache, args.keyring, args.output, args.sources)
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
