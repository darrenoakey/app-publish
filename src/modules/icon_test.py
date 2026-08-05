import hashlib
import importlib
import json
from pathlib import Path

from PIL import Image

import modules.icon as icon_module
from config import ICON_SIZES_IOS
from modules.icon import (
    GENERATE_IMAGE_CLI,
    check_existing_icons,
    generate_icon_prompt,
    generate_master_icon,
    icon_cache_path,
    is_valid_master_icon,
    resize_icons,
    run,
)
from state import ProjectState

importlib.reload(icon_module)


# ##################################################################
# durable codex icon generation
# proves the real canonical service path produces the exact artifact and
# recovers the same durable operation identity after local output loss
def test_generate_master_icon_via_durable_codex_queue() -> None:
    project_path = Path(__file__).resolve().parents[2] / "output" / "testing" / "icon-canary"
    project_path.mkdir(parents=True, exist_ok=True)
    master_icon = project_path / "assets" / "icon-1024.png"
    if master_icon.exists():
        master_icon.unlink()

    state = ProjectState(
        project_path=str(project_path),
        project_name="icon-canary",
        app_name="Codex Queue Canary",
        app_description="A durable image-routing verification utility",
    )
    result = generate_master_icon(project_path, state)

    assert result == master_icon
    with Image.open(master_icon) as image:
        image.load()
        assert image.format == "PNG"
        assert image.size == (1024, 1024)
    first_digest = hashlib.sha256(master_icon.read_bytes()).hexdigest()
    cached_icon = icon_cache_path(generate_icon_prompt(state))
    assert is_valid_master_icon(cached_icon)
    assert GENERATE_IMAGE_CLI == "/Users/darrenoakey/bin/generate_image"

    master_icon.unlink()
    recovered = generate_master_icon(project_path, state)

    assert recovered == master_icon
    assert hashlib.sha256(master_icon.read_bytes()).hexdigest() == first_digest
    with Image.open(master_icon) as image:
        image.load()
        assert image.format == "PNG"
        assert image.size == (1024, 1024)


def test_local_icon_validation_resize_and_existing_asset_detection(tmp_path) -> None:
    master = tmp_path / "master.png"
    Image.new("RGB", (1024, 1024), (20, 80, 180)).save(master, "PNG")
    assert is_valid_master_icon(master) is True
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")
    assert is_valid_master_icon(invalid) is False
    assert is_valid_master_icon(tmp_path / "absent.png") is False

    xcode = tmp_path / "Reader" / "Reader.xcodeproj"
    assets = xcode.parent / "Assets.xcassets" / "AppIcon.appiconset"
    assets.mkdir(parents=True)
    state = ProjectState(metadata={"xcode_project": str(xcode)})
    assert resize_icons(master, tmp_path, state) is True
    contents = json.loads((assets / "Contents.json").read_text())
    assert len(contents["images"]) == len(ICON_SIZES_IOS)
    for item in contents["images"]:
        with Image.open(assets / item["filename"]) as image:
            expected = int(float(item["size"].split("x", 1)[0]) * int(item["scale"].removesuffix("x")))
            assert image.size == (expected, expected)

    assert check_existing_icons(tmp_path, state) is True
    assert run(tmp_path, state) is True
    assert state.metadata["icon_generated"] is True
    assert state.metadata["icon_existing"] is True


def test_existing_icon_scan_handles_large_and_unreadable_local_pngs(tmp_path) -> None:
    xcode = tmp_path / "Reader.xcodeproj"
    app_icons = tmp_path / "Assets.xcassets" / "AppIcon.appiconset"
    app_icons.mkdir(parents=True)
    (app_icons / "broken.png").write_text("broken")
    Image.new("RGBA", (1024, 1024), (0, 0, 0, 255)).save(app_icons / "custom.png", "PNG")
    state = ProjectState(metadata={"xcode_project": str(xcode)})
    assert check_existing_icons(tmp_path, state) is True
    assert check_existing_icons(tmp_path, ProjectState()) is False
