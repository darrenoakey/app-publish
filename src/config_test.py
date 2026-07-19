from config import (
    ICON_SIZE,
    ICON_SIZES_IOS,
    OPTIONAL_STEPS,
    PIPELINE_STEPS,
    SCREENSHOT_DEVICES,
)


def test_pipeline_and_asset_configuration_is_internally_consistent() -> None:
    assert len(PIPELINE_STEPS) == len(set(PIPELINE_STEPS))
    assert OPTIONAL_STEPS < set(PIPELINE_STEPS)
    assert PIPELINE_STEPS[-1] == "deploy"
    assert ICON_SIZE == 1024
    assert any(size == ICON_SIZE and idiom == "ios-marketing" for size, _, idiom, _ in ICON_SIZES_IOS)
    assert len(SCREENSHOT_DEVICES) == len(set(SCREENSHOT_DEVICES)) == 4
