from pathlib import Path
import hashlib

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState, load_state, save_state
from config import SCREENSHOT_DEVICES
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    llm_chat,
    ensure_dir,
    write_file,
    file_exists,
    dir_exists,
    claude_agent_task,
)


# ##################################################################
# compute image hash
# compute a perceptual hash for an image using average hash algorithm
# similar images will have similar hashes
def compute_image_hash(image_path: Path, block_size: int = 8) -> str:
    try:
        from PIL import Image
    except ImportError:
        # Fall back to file hash if PIL not available
        return hashlib.md5(image_path.read_bytes()).hexdigest()

    try:
        img = Image.open(image_path)
        # Convert to grayscale and resize to small square
        img = img.convert('L').resize((block_size, block_size), Image.Resampling.LANCZOS)

        # Get pixel data
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)

        # Create binary hash based on whether pixel is above/below average
        bits = ''.join('1' if p > avg else '0' for p in pixels)
        return hex(int(bits, 2))[2:].zfill(16)
    except Exception as e:
        # Fall back to file hash
        return hashlib.md5(image_path.read_bytes()).hexdigest()


# ##################################################################
# hamming distance
# calculate hamming distance between two hashes
def hamming_distance(hash1: str, hash2: str) -> int:
    if len(hash1) != len(hash2):
        return 64  # Max distance
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        xor = val1 ^ val2
        return bin(xor).count('1')
    except ValueError:
        return 64


# ##################################################################
# are images similar
# check if two images are visually similar using perceptual hashing
# lower threshold = more strict (fewer matches), higher threshold = more lenient (more matches)
# default threshold of 10 catches very similar images
def are_images_similar(path1: Path, path2: Path, threshold: int = 10) -> bool:
    hash1 = compute_image_hash(path1)
    hash2 = compute_image_hash(path2)
    distance = hamming_distance(hash1, hash2)
    return distance <= threshold


# ##################################################################
# remove duplicate screenshots
# remove duplicate/similar screenshots from directory
# returns number of duplicates removed
def remove_duplicate_screenshots(screenshots_dir: Path) -> int:
    screenshots = sorted(screenshots_dir.glob("*.png"))
    if len(screenshots) < 2:
        return 0

    print_info("Checking screenshots for duplicates...")

    # Group by device type (iPhone vs iPad)
    iphone_shots = [s for s in screenshots if "iPhone" in s.name]
    ipad_shots = [s for s in screenshots if "iPad" in s.name]

    removed = 0

    for shots in [iphone_shots, ipad_shots]:
        if len(shots) < 2:
            continue

        # Compare each pair
        to_remove = set()
        for i, shot1 in enumerate(shots):
            if shot1 in to_remove:
                continue
            for shot2 in shots[i+1:]:
                if shot2 in to_remove:
                    continue
                if are_images_similar(shot1, shot2):
                    # Keep the first one, mark second for removal
                    print_warning(f"  Duplicate detected: {shot2.name} similar to {shot1.name}")
                    to_remove.add(shot2)

        # Remove duplicates
        for path in to_remove:
            print_info(f"  Removing duplicate: {path.name}")
            path.unlink()
            removed += 1

    if removed > 0:
        print_info(f"Removed {removed} duplicate screenshots")
    else:
        print_success("No duplicate screenshots found")

    return removed


# ##################################################################
# detect widget extension
# detect if the project has a widget extension and find relevant widget views
# returns dict with has_widget (bool), extension_dir (path or none),
# widget_view (str or none - name of the widget entry view),
# preview_view (str or none - name of any preview view)
def detect_widget_extension(project_path: Path) -> dict:
    result = {
        "has_widget": False,
        "extension_dir": None,
        "widget_view": None,
        "preview_view": None,
        "widget_family": "systemSmall",
    }

    # Look for widget extension directories
    widget_dirs = list(project_path.glob("*Widget*")) + list(project_path.glob("*widget*"))
    widget_dirs = [d for d in widget_dirs if d.is_dir() and not d.name.endswith(('.xcodeproj', '.xcworkspace', '.app', '.appex', '.build'))]

    for widget_dir in widget_dirs:
        swift_files = list(widget_dir.glob("*.swift"))
        for swift_file in swift_files:
            try:
                content = swift_file.read_text()
                # Check for WidgetKit imports
                if "import WidgetKit" in content:
                    result["has_widget"] = True
                    result["extension_dir"] = widget_dir

                    # Find widget entry view (struct that has View and uses entry)
                    import re
                    # Look for pattern: struct XxxEntryView: View
                    entry_view_match = re.search(r'struct\s+(\w+EntryView)\s*:\s*View', content)
                    if entry_view_match:
                        result["widget_view"] = entry_view_match.group(1)

                    # Look for widget family
                    if ".systemMedium" in content:
                        result["widget_family"] = "systemMedium"
                    elif ".systemLarge" in content:
                        result["widget_family"] = "systemLarge"

                    break
            except Exception:
                continue

    # Also check main app for preview views
    main_app_dir = project_path / project_path.name
    if main_app_dir.exists():
        for swift_file in main_app_dir.glob("*.swift"):
            try:
                content = swift_file.read_text()
                # Look for WidgetPreview or similar
                import re
                preview_match = re.search(r'struct\s+(\w*[Ww]idget[Pp]review\w*)\s*:\s*View', content)
                if preview_match:
                    result["preview_view"] = preview_match.group(1)
                    break
            except Exception:
                continue

    return result


# ##################################################################
# generate widget sample data
# analyze widget source code and generate appropriate sample data using LLM
def generate_widget_sample_data(project_path: Path, state: ProjectState, widget_info: dict) -> str:
    # Read widget source files
    widget_dir = widget_info.get("extension_dir")
    if not widget_dir:
        return _get_generic_widget_content()

    widget_source = []
    for swift_file in Path(widget_dir).glob("*.swift"):
        try:
            content = swift_file.read_text()
            widget_source.append(f"// {swift_file.name}\n{content}")
        except Exception:
            continue

    if not widget_source:
        return _get_generic_widget_content()

    combined_source = "\n\n".join(widget_source)[:30000]  # Limit token usage

    prompt = f"""Analyze this iOS widget source code and generate Swift code that creates the widget view with realistic sample data.

APP INFO:
- Name: {state.app_name}
- Description: {state.app_description[:300] if state.app_description else 'A widget app'}

WIDGET SOURCE CODE:
{combined_source}

Generate ONLY the Swift code for the widgetContent computed property that instantiates the widget's entry view with sample data.
The code should:
1. Use the actual view name found in the source (e.g., WidgetEntryView, SharedWidgetView, etc.)
2. Provide realistic sample data appropriate for this widget's purpose
3. Include any helper functions needed (like generating sample history data)

Respond with ONLY the Swift code, starting with:
@ViewBuilder
var widgetContent: some View {{
"""

    response = llm_chat(prompt)
    if response and "@ViewBuilder" in response:
        # Extract just the code
        start = response.find("@ViewBuilder")
        if start >= 0:
            return response[start:]

    return _get_generic_widget_content()


# ##################################################################
# get generic widget content
# fallback generic widget content for unknown widget types
def _get_generic_widget_content() -> str:
    return '''@ViewBuilder
    var widgetContent: some View {
        // Generic widget placeholder - actual widget view could not be detected
        VStack(spacing: 8) {
            Image(systemName: "app.fill")
                .font(.largeTitle)
                .foregroundColor(.blue)
            Text("Widget Preview")
                .font(.headline)
            Text("Sample Data")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
    }'''


# ##################################################################
# create widget screenshot harness
# create a swift file that renders the widget in a harness for screenshotting
# creates a view that displays the widget at the correct size with sample data
def create_widget_screenshot_harness(project_path: Path, state: ProjectState, widget_info: dict) -> Path:
    harness_dir = project_path / "WidgetScreenshotHarness"
    ensure_dir(harness_dir)

    # Widget sizes for different families
    widget_sizes = {
        "systemSmall": (170, 170),
        "systemMedium": (364, 170),
        "systemLarge": (364, 382),
    }
    width, height = widget_sizes.get(widget_info.get("widget_family", "systemSmall"), (170, 170))

    # Generate appropriate sample data for this widget
    widget_content_code = generate_widget_sample_data(project_path, state, widget_info)

    harness_code = f'''import SwiftUI
import UIKit

// Widget Screenshot Harness - Auto-generated by app-publish
// This creates a standalone view that renders the widget for screenshots

struct WidgetScreenshotHarness: View {{
    var body: some View {{
        VStack {{
            Spacer()

            // Render the widget in a frame matching widget size
            widgetContent
                .frame(width: {width}, height: {height})
                .clipShape(RoundedRectangle(cornerRadius: 24))
                .shadow(color: .black.opacity(0.2), radius: 10, x: 0, y: 5)

            Spacer()
        }}
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            LinearGradient(
                colors: [Color.blue.opacity(0.3), Color.purple.opacity(0.3)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
    }}

    {widget_content_code}
}}

// Screenshot capture helper
class WidgetScreenshotter {{
    static func captureWidget(to path: String, completion: @escaping (Bool) -> Void) {{
        DispatchQueue.main.async {{
            let harness = WidgetScreenshotHarness()
            let controller = UIHostingController(rootView: harness)
            controller.view.frame = CGRect(x: 0, y: 0, width: 400, height: 400)

            // Force layout
            controller.view.layoutIfNeeded()

            // Render to image
            let renderer = UIGraphicsImageRenderer(bounds: controller.view.bounds)
            let image = renderer.image {{ ctx in
                controller.view.drawHierarchy(in: controller.view.bounds, afterScreenUpdates: true)
            }}

            // Crop to widget area (centered)
            let widgetSize = CGSize(width: {width}, height: {height})
            let cropRect = CGRect(
                x: (controller.view.bounds.width - widgetSize.width) / 2,
                y: (controller.view.bounds.height - widgetSize.height) / 2,
                width: widgetSize.width,
                height: widgetSize.height
            )

            if let cgImage = image.cgImage?.cropping(to: cropRect) {{
                let croppedImage = UIImage(cgImage: cgImage)
                if let data = croppedImage.pngData() {{
                    do {{
                        try data.write(to: URL(fileURLWithPath: path))
                        completion(true)
                        return
                    }} catch {{
                        print("Failed to write widget screenshot: \\(error)")
                    }}
                }}
            }}
            completion(false)
        }}
    }}
}}
'''

    harness_path = harness_dir / "WidgetScreenshotHarness.swift"
    write_file(harness_path, harness_code)
    return harness_path


# ##################################################################
# capture widget via harness
# capture widget screenshot by detecting the widget extension and its view structure,
# creating a ui test that displays the widget preview, running the test to capture,
# and cropping the screenshot to just the widget area
def capture_widget_via_harness(project_path: Path, state: ProjectState, screenshots_dir: Path) -> bool:
    print_info("Detecting widget extension...")

    widget_info = detect_widget_extension(project_path)
    if not widget_info["has_widget"]:
        print_info("No widget extension detected")
        return False

    print_info(f"Found widget extension in: {widget_info['extension_dir']}")
    if widget_info.get("preview_view"):
        print_info(f"Found existing preview view: {widget_info['preview_view']}")

    xcode_project = state.metadata.get("xcode_project", "")

    # Widget sizes for cropping
    widget_sizes = {
        "systemSmall": (170, 170),
        "systemMedium": (364, 170),
        "systemLarge": (364, 382),
    }
    widget_width, widget_height = widget_sizes.get(widget_info.get("widget_family", "systemSmall"), (170, 170))

    # Find UITests directory
    ui_test_dirs = list(project_path.glob("*UITests"))
    if not ui_test_dirs:
        print_warning("No UITests directory found")
        return False

    ui_test_dir = ui_test_dirs[0]

    # Create a UI test file that displays and captures the widget
    widget_test_code = f'''import XCTest

/// Widget Screenshot Test - Auto-generated by app-publish
/// This test displays the widget preview and captures a screenshot
class WidgetScreenshotTests: XCTestCase {{

    static let screenshotDir = "{screenshots_dir}"
    static let widgetWidth: CGFloat = {widget_width}
    static let widgetHeight: CGFloat = {widget_height}

    var app: XCUIApplication!

    override func setUpWithError() throws {{
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--widget-screenshot-mode"]
        app.launch()
    }}

    func testCaptureWidgetPreview() throws {{
        // Wait for app to load
        Thread.sleep(forTimeInterval: 2)

        // Look for widget preview element (accessibility identifier)
        let widgetPreview = app.otherElements["WidgetPreviewContainer"]

        if widgetPreview.exists {{
            // Take screenshot of just the widget area
            let screenshot = widgetPreview.screenshot()
            saveScreenshot(screenshot.pngRepresentation, name: "widget")
        }} else {{
            // Fallback: take full screenshot and crop later
            let screenshot = XCUIScreen.main.screenshot()
            saveScreenshot(screenshot.pngRepresentation, name: "widget_full")
        }}
    }}

    private func saveScreenshot(_ data: Data, name: String) {{
        let deviceName = getDeviceName()
        let filename = "\\(deviceName)-\\(name).png"
        let path = "\\(Self.screenshotDir)/\\(filename)"

        try? FileManager.default.createDirectory(
            atPath: Self.screenshotDir,
            withIntermediateDirectories: true
        )

        do {{
            try data.write(to: URL(fileURLWithPath: path))
            print("Widget screenshot saved to: \\(path)")
        }} catch {{
            print("Failed to save widget screenshot: \\(error)")
        }}
    }}

    private func getDeviceName() -> String {{
        let screenHeight = UIScreen.main.nativeBounds.height
        if UIDevice.current.model.contains("iPad") {{
            return "iPad-Pro-13-inch"
        }} else if screenHeight >= 2796 {{
            return "iPhone-16-Pro-Max"
        }}
        return "iPhone"
    }}
}}
'''

    widget_test_path = ui_test_dir / "WidgetScreenshotTests.swift"
    write_file(widget_test_path, widget_test_code)
    print_info(f"Created widget test: {widget_test_path}")

    # Now use agent to:
    # 1. Add widget preview mode to the app (if not already present)
    # 2. Run the UI test
    # 3. Crop the screenshot if needed
    task = f"""TASK: Run the widget screenshot test and ensure we get a proper widget screenshot.

PROJECT INFO:
- App name: {state.app_name}
- Xcode project: {xcode_project}
- Screenshots directory: {screenshots_dir}
- Widget size: {widget_width}x{widget_height}
- UI Test file created: {widget_test_path}

STEP 1: Add Widget Preview Mode to the App
Check if the app already has a WidgetPreview view. If so, modify the app to display it when launched with "--widget-screenshot-mode":

First check for existing preview:
```bash
grep -l "WidgetPreview\\|SharedWidgetView" "{project_path}/{state.app_name}/"*.swift
```

If found (like in ContentView.swift or a dedicated file), modify the app to:
1. Check for the launch argument "--widget-screenshot-mode"
2. When present, show a full-screen view with the widget centered
3. Add accessibility identifier "WidgetPreviewContainer" to the widget view

Example modification to add to the main App struct or ContentView:
```swift
// In the main view, check for widget screenshot mode
if ProcessInfo.processInfo.arguments.contains("--widget-screenshot-mode") {{
    WidgetPreviewForScreenshot()
        .accessibilityIdentifier("WidgetPreviewContainer")
}} else {{
    // Normal app content
}}
```

STEP 2: Add the WidgetPreviewForScreenshot View
Create a view that displays the widget preview with sample data at correct size ({widget_width}x{widget_height}):
```swift
struct WidgetPreviewForScreenshot: View {{
    var body: some View {{
        ZStack {{
            // Nice background for the screenshot
            LinearGradient(
                colors: [Color.blue.opacity(0.3), Color.purple.opacity(0.3)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            // The widget at correct size
            SharedWidgetView(
                symbol: "AAPL",
                companyName: "Apple Inc.",
                price: 178.50,
                priceCurrency: "USD",
                portfolioValue: 17850,
                portfolioCurrency: "USD",
                history: SampleData.stockHistory,
                changeAmount: 3.25,
                changePercent: 1.85,
                fxRate: nil
            )
            .frame(width: {widget_width}, height: {widget_height})
            .clipShape(RoundedRectangle(cornerRadius: 24))
            .shadow(radius: 10)
        }}
    }}
}}
```

STEP 3: Run the Widget Screenshot Test
```bash
xcodebuild test \\
    -project "{xcode_project}" \\
    -scheme "{state.app_name}" \\
    -destination "platform=iOS Simulator,name=iPhone 16 Pro Max" \\
    -only-testing:"{state.app_name}UITests/WidgetScreenshotTests" \\
    2>&1 | tail -30
```

STEP 4: Verify and Crop if Needed
Check if the screenshot was created:
```bash
ls -la "{screenshots_dir}/"*widget*.png
```

If a "widget_full" screenshot was created (not "widget"), use Python to crop it:
```python
from PIL import Image
img = Image.open("{screenshots_dir}/iPhone-16-Pro-Max-widget_full.png")
# Center crop to widget size
left = (img.width - {widget_width}) // 2
top = (img.height - {widget_height}) // 2
cropped = img.crop((left, top, left + {widget_width}, top + {widget_height}))
cropped.save("{screenshots_dir}/iPhone-16-Pro-Max-widget.png")
```

SUCCESS CRITERIA:
- A widget screenshot exists at {screenshots_dir}/iPhone-16-Pro-Max-widget.png
- The screenshot shows the widget with sample stock data (AAPL, green chart)
"""

    success, output = claude_agent_task(task, project_path, timeout=900)

    # Check if widget screenshot was created
    widget_screenshots = list(screenshots_dir.glob("*widget*.png")) + list(screenshots_dir.glob("*Widget*.png"))
    if widget_screenshots:
        # If we have a "widget_full" screenshot, crop it
        for ss in widget_screenshots:
            if "full" in ss.name.lower():
                crop_widget_screenshot(ss, screenshots_dir, widget_width, widget_height)

        widget_screenshots = list(screenshots_dir.glob("*widget*.png"))
        if widget_screenshots:
            print_success(f"Captured widget screenshot: {widget_screenshots[0].name}")
            return True

    return False


# ##################################################################
# crop widget screenshot
# crop a full screenshot to highlight the widget area
# for small widgets (like systemsmall at 170x170), we don't just crop to that size
# since it would be too small for app store, instead we find the widget area and
# create an app store-sized image with the widget prominently displayed
def crop_widget_screenshot(source_path: Path, output_dir: Path, widget_width: int, widget_height: int) -> bool:
    try:
        from PIL import Image, ImageDraw

        img = Image.open(source_path)

        # App Store minimum useful size - we want at least 1290 width for iPhone
        MIN_WIDTH = 1290
        MIN_HEIGHT = 2796

        # If the source image is already at device resolution, the widget preview
        # is likely embedded in the app UI - in this case, the full screenshot
        # is more useful than a tiny crop
        if img.width >= MIN_WIDTH and img.height >= MIN_HEIGHT:
            # The full screenshot likely already shows the widget preview in context
            # Just rename it and keep as-is
            new_name = source_path.name.replace("_full", "").replace("-full", "")
            output_path = output_dir / new_name
            img.save(output_path)
            print_info(f"Widget screenshot (full context): {output_path.name}")
            source_path.unlink()
            return True

        # For smaller images, try to create a nice presentation
        # Scale the widget to be prominent (about 40% of screen width)
        target_widget_width = int(MIN_WIDTH * 0.4)
        scale = target_widget_width / widget_width
        scaled_widget_height = int(widget_height * scale)

        # Crop widget area from center of source
        left = (img.width - widget_width) // 2
        top = (img.height - widget_height) // 2
        right = left + widget_width
        bottom = top + widget_height

        # Ensure bounds are valid
        left = max(0, left)
        top = max(0, top)
        right = min(img.width, right)
        bottom = min(img.height, bottom)

        widget_crop = img.crop((left, top, right, bottom))

        # Scale up the widget
        widget_scaled = widget_crop.resize(
            (target_widget_width, scaled_widget_height),
            Image.Resampling.LANCZOS
        )

        # Create App Store sized canvas with gradient background
        canvas = Image.new('RGB', (MIN_WIDTH, MIN_HEIGHT), (102, 126, 234))

        # Create a simple gradient
        draw = ImageDraw.Draw(canvas)
        for y in range(MIN_HEIGHT):
            # Gradient from purple-blue to purple
            r = int(102 + (118 - 102) * y / MIN_HEIGHT)
            g = int(126 + (75 - 126) * y / MIN_HEIGHT)
            b = int(234 + (162 - 234) * y / MIN_HEIGHT)
            draw.line([(0, y), (MIN_WIDTH, y)], fill=(r, g, b))

        # Center the widget on canvas
        paste_x = (MIN_WIDTH - target_widget_width) // 2
        paste_y = (MIN_HEIGHT - scaled_widget_height) // 2

        # Add rounded corners effect to widget (approximate with mask)
        canvas.paste(widget_scaled, (paste_x, paste_y))

        # Save
        new_name = source_path.name.replace("_full", "").replace("-full", "")
        output_path = output_dir / new_name
        canvas.save(output_path)

        print_info(f"Cropped widget screenshot: {output_path.name}")
        source_path.unlink()

        return True
    except Exception as e:
        print_warning(f"Failed to crop widget screenshot: {e}")
        return False


# ##################################################################
# find ui test target
# find ui test target if it exists, returns (exists, target_name)
def find_ui_test_target(project_path: Path, state: ProjectState) -> tuple[bool, str]:
    xcode_project = state.metadata.get("xcode_project", "")
    if not xcode_project:
        return False, ""

    project_dir = Path(xcode_project).parent

    # Look for existing UI test directory
    ui_test_dirs = list(project_dir.glob("*UITests"))
    if ui_test_dirs:
        target_name = ui_test_dirs[0].name
        print_info(f"UI test target exists: {target_name}")
        return True, target_name

    return False, ""


# ##################################################################
# generate screenshot tests with agent
# use claude agent sdk to analyze the app, generate ui tests, add them to xcode,
# and run them until screenshots are successfully captured
# uses a persistent claude agent session to analyze swift code, generate xcuitest code,
# add test file to xcode project target, run tests on simulators, and iterate until
# screenshots appear in the target directory
def generate_screenshot_tests_with_agent(project_path: Path, state: ProjectState) -> bool:
    print_info("Using Claude Agent SDK to generate screenshot UI tests...")

    screenshots_dir = project_path / "fastlane" / "screenshots" / "en-US"
    xcode_project = state.metadata.get("xcode_project", "")

    # Detect if this is a widget app
    is_widget_app = "widget" in state.app_name.lower() or "widget" in (state.app_description or "").lower()
    widget_note = """
IMPORTANT - WIDGET APP:
This is a widget app! The main feature is the iOS home screen widget.
- Capture screenshots that show the widget preview/configuration screen
- Show different widget states (with data, different stocks, etc.)
- If there's a widget preview in the app, capture that prominently
- Show the setup/configuration flow
""" if is_widget_app else ""

    task = f"""Generate and run screenshot UI tests for this iOS app. Your goal is to create UNIQUE, DIVERSE PNG screenshot files.

PROJECT INFO:
- App name: {state.app_name}
- Bundle ID: {state.bundle_id}
- Description: {state.app_description[:500] if state.app_description else 'iOS app'}
- Xcode project: {xcode_project}
- Screenshots MUST be saved to: {screenshots_dir}
{widget_note}
CRITICAL REQUIREMENTS:
1. Each screenshot MUST show a DIFFERENT screen or state - no duplicates!
2. Screenshots should demonstrate the app's KEY FEATURES
3. Show variety: different data, different screens, different configurations
4. Each screenshot should be visually distinct from the others

SUCCESS CRITERIA: PNG files must exist in {screenshots_dir} when you're done.

STEP 1: ANALYZE THE APP
Read the Swift source files to understand the UI structure.

STEP 2: CREATE SCREENSHOT TEST FILE
Find the UITests directory (e.g., "*UITests") and create ScreenshotTests.swift:

```swift
import XCTest

@MainActor
class ScreenshotTests: XCTestCase {{
    var app: XCUIApplication!
    static let screenshotDir = "{screenshots_dir}"

    var deviceName: String {{
        let screenHeight = UIScreen.main.nativeBounds.height
        if UIDevice.current.model.contains("iPad") {{
            return "iPad Pro 13-inch"
        }} else if screenHeight >= 2796 {{
            return "iPhone 16 Pro Max"
        }}
        return "iPhone"
    }}

    override func setUpWithError() throws {{
        continueAfterFailure = false
        app = XCUIApplication()
        app.launch()
        Thread.sleep(forTimeInterval: 2)
    }}

    func test01_MainScreen() {{
        saveScreenshot("01_main")
    }}

    // Add more test methods for different screens...

    private func saveScreenshot(_ name: String) {{
        let screenshot = XCUIScreen.main.screenshot()
        let path = "\\(Self.screenshotDir)/\\(deviceName)-\\(name).png"
        try? FileManager.default.createDirectory(atPath: Self.screenshotDir, withIntermediateDirectories: true)
        try? screenshot.pngRepresentation.write(to: URL(fileURLWithPath: path))
    }}
}}
```

STEP 3: VERIFY TEST FILE IS IN XCODE PROJECT
Check if the test file is included in the project.pbxproj. If not, you may need to add it.
First, check current state:
```bash
grep -l "ScreenshotTests.swift" "{xcode_project}/project.pbxproj" || echo "NOT FOUND"
```

STEP 4: RUN TESTS ON IPHONE SIMULATOR
```bash
xcodebuild test \\
  -project "{xcode_project}" \\
  -scheme "{state.app_name}" \\
  -destination "platform=iOS Simulator,name=iPhone 16 Pro Max" \\
  -only-testing:"{state.app_name}UITests/ScreenshotTests" \\
  2>&1 | tail -50
```

If that fails, try without -only-testing to run all UI tests:
```bash
xcodebuild test \\
  -project "{xcode_project}" \\
  -scheme "{state.app_name}" \\
  -destination "platform=iOS Simulator,name=iPhone 16 Pro Max" \\
  2>&1 | tail -50
```

STEP 5: CHECK FOR SCREENSHOTS
```bash
ls -la "{screenshots_dir}/"*.png 2>/dev/null || echo "NO SCREENSHOTS YET"
```

STEP 6: IF NO SCREENSHOTS - DEBUG AND FIX
- Check the xcodebuild output for errors
- Common issues:
  - Test file not in project (add it)
  - Wrong scheme name (list schemes with: xcodebuild -list -project "{xcode_project}")
  - UI elements not found (simplify the test)
  - Directory doesn't exist (create it)

STEP 7: ITERATE UNTIL SUCCESS
Keep fixing issues and re-running until PNG files appear in the screenshots directory.
If tests keep failing, simplify to just:
```swift
func test01_MainScreen() {{
    Thread.sleep(forTimeInterval: 3)
    saveScreenshot("01_main")
}}
```

STEP 8: RUN ON IPAD TOO
Once iPhone works, also run on iPad:
```bash
xcodebuild test \\
  -project "{xcode_project}" \\
  -scheme "{state.app_name}" \\
  -destination "platform=iOS Simulator,name=iPad Pro 13-inch (M4)" \\
  2>&1 | tail -30
```

FINAL CHECK:
```bash
ls -la "{screenshots_dir}/"*.png
```

You MUST have PNG files in {screenshots_dir} before finishing.
"""

    success, output = claude_agent_task(task, project_path, timeout=1200)  # 20 min timeout

    # Check if screenshots were generated regardless of reported success
    ensure_dir(screenshots_dir)
    screenshots = list(screenshots_dir.glob("*.png"))
    if screenshots:
        print_success(f"Agent generated {len(screenshots)} screenshots")
        return True
    else:
        print_warning(f"Agent completed but no screenshots found in {screenshots_dir}")
        if output:
            # Show last part of output for debugging
            print_info(f"Agent output (last 500 chars): ...{output[-500:]}" if len(output) > 500 else f"Agent output: {output}")
        return False


# ##################################################################
# run ui tests for screenshots
# run ui tests to capture screenshots
def run_ui_tests_for_screenshots(project_path: Path, state: ProjectState, target_name: str) -> bool:
    xcode_project = state.metadata.get("xcode_project", "")
    if not xcode_project:
        return False

    screenshots_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(screenshots_dir)

    # Devices to capture screenshots on
    devices = [
        ("iPhone 16 Pro Max", "APP_IPHONE_67"),
        ("iPad Pro 13-inch (M4)", "APP_IPAD_PRO_3GEN_129"),
    ]

    print_info("Running UI tests for screenshots...")

    for device_name, display_type in devices:
        print_info(f"  Testing on {device_name}...")

        # Run xcodebuild test
        ret_code, output = exec_cmd([
            "xcodebuild", "test",
            "-project", xcode_project,
            "-scheme", target_name.replace("UITests", "").strip() or state.project_name,
            "-destination", f"platform=iOS Simulator,name={device_name}",
            "-testPlan", target_name,
            "-only-testing", target_name,
        ], cwd=project_path, timeout=600)

        if ret_code != 0:
            # Try without -testPlan
            ret_code, output = exec_cmd([
                "xcodebuild", "test",
                "-project", xcode_project,
                "-scheme", state.project_name,
                "-destination", f"platform=iOS Simulator,name={device_name}",
                "-only-testing", target_name,
            ], cwd=project_path, timeout=600)

        if ret_code == 0:
            print_success(f"    Tests completed on {device_name}")
        else:
            print_warning(f"    Tests failed on {device_name}")

    # Check if screenshots were generated
    screenshots = list(screenshots_dir.glob("*.png"))
    if screenshots:
        print_success(f"Captured {len(screenshots)} screenshots")
        return True
    else:
        print_warning("No screenshots were captured by UI tests")
        return False


# ##################################################################
# analyze screenshot scenarios
# analyze app source code to determine canonical screenshots for this app
# returns list of scenario dicts and saves to state.metadata for reuse
def analyze_screenshot_scenarios(project_path: Path, state: ProjectState) -> list[dict]:
    # Check if scenarios already cached in state
    if state.metadata.get("screenshot_scenarios"):
        print_info("Using cached screenshot scenarios from state")
        return state.metadata["screenshot_scenarios"]

    print_info("Analyzing app source code for screenshot scenarios...")

    # Collect source files based on project type
    source_content = []

    # Swift files for iOS apps
    swift_files = list(project_path.glob("**/*.swift"))
    # Exclude build artifacts and derived data
    swift_files = [f for f in swift_files if not any(x in str(f) for x in [
        ".build", "DerivedData", "Pods", ".xcodeproj", "WidgetScreenshotHarness"
    ])]

    # Web files for Capacitor/web apps
    web_files = (
        list(project_path.glob("**/*.html")) +
        list(project_path.glob("**/*.tsx")) +
        list(project_path.glob("**/*.vue")) +
        list(project_path.glob("**/*.jsx"))
    )
    # Exclude node_modules and build artifacts
    web_files = [f for f in web_files if not any(x in str(f) for x in [
        "node_modules", "dist", "build", ".next"
    ])]

    all_files = swift_files + web_files

    # Read source files (limit to avoid token overflow)
    max_content_size = 50000  # ~50KB of source to analyze
    current_size = 0

    for source_file in all_files[:30]:  # Max 30 files
        try:
            content = source_file.read_text()
            if current_size + len(content) > max_content_size:
                # Truncate this file
                content = content[:max_content_size - current_size]
                source_content.append(f"\n--- {source_file.name} (truncated) ---\n{content}")
                break
            source_content.append(f"\n--- {source_file.name} ---\n{content}")
            current_size += len(content)
        except Exception:
            continue

    if not source_content:
        print_warning("No source files found to analyze")
        return _get_default_scenarios(state)

    combined_source = "\n".join(source_content)

    prompt = f"""Analyze this iOS/mobile app source code and determine the 5-6 most important screenshots to capture for the App Store.

APP INFO:
- Name: {state.app_name}
- Description: {state.app_description[:500] if state.app_description else 'A mobile application'}

SOURCE CODE:
{combined_source}

Based on the actual screens and features in this code, identify the most important screenshots.
For each screenshot, provide:
1. A short filename-safe name (e.g., "01_main", "02_gameplay", "03_results")
2. A description of what the screenshot should show
3. Navigation steps to reach that screen (if applicable)

Respond in this exact JSON format (no other text):
[
  {{"name": "01_main", "description": "Main screen showing...", "navigation": "Launch app"}},
  {{"name": "02_feature", "description": "...", "navigation": "..."}},
  ...
]
"""

    response = llm_chat(prompt)
    if not response:
        return _get_default_scenarios(state)

    # Parse JSON response
    import json
    try:
        # Find JSON array in response
        start = response.find("[")
        end = response.rfind("]") + 1
        if start >= 0 and end > start:
            scenarios = json.loads(response[start:end])
            if scenarios and isinstance(scenarios, list):
                # Validate structure
                valid_scenarios = []
                for s in scenarios[:6]:
                    if isinstance(s, dict) and "name" in s and "description" in s:
                        valid_scenarios.append({
                            "name": s.get("name", ""),
                            "description": s.get("description", ""),
                            "navigation": s.get("navigation", ""),
                            "priority": s.get("priority", 1)
                        })
                if valid_scenarios:
                    # Cache in state
                    state.metadata["screenshot_scenarios"] = valid_scenarios
                    print_success(f"Identified {len(valid_scenarios)} screenshot scenarios")
                    return valid_scenarios
    except json.JSONDecodeError:
        pass

    print_warning("Could not parse LLM response, using defaults")
    return _get_default_scenarios(state)


# ##################################################################
# get default scenarios
# fallback scenarios when source analysis fails
def _get_default_scenarios(state: ProjectState) -> list[dict]:
    return [
        {"name": "01_main", "description": "Main app screen", "navigation": "Launch app", "priority": 1},
        {"name": "02_feature", "description": "Key feature in action", "navigation": "", "priority": 1},
        {"name": "03_detail", "description": "Detail or result view", "navigation": "", "priority": 1},
        {"name": "04_settings", "description": "Settings or preferences", "navigation": "", "priority": 2},
        {"name": "05_about", "description": "About or info screen", "navigation": "", "priority": 2},
    ]


# ##################################################################
# generate screenshot scenarios
# use ai to generate screenshot scenarios (wrapper for analyze_screenshot_scenarios)
def generate_screenshot_scenarios(project_path: Path, state: ProjectState) -> list[str]:
    # Use the full source analysis function
    scenarios = analyze_screenshot_scenarios(project_path, state)

    # Return just the descriptions for backward compatibility
    return [s["description"] for s in scenarios]


# ##################################################################
# run fastlane snapshot
# run fastlane snapshot to capture screenshots
def run_fastlane_snapshot(project_path: Path) -> bool:
    snapfile = project_path / "fastlane" / "Snapfile"
    if not file_exists(snapfile):
        print_warning("Snapfile not found, skipping automated screenshots")
        return True

    print_info("Running fastlane snapshot...")
    ret_code, output = exec_cmd(
        ["fastlane", "snapshot"],
        cwd=project_path,
        timeout=600,
    )

    if ret_code != 0:
        print_warning(f"Snapshot failed: {output}")
        # Don't fail - screenshots can be added manually
        return True

    print_success("Screenshots captured")
    return True


# ##################################################################
# create placeholder screenshots
# create placeholder info for manual screenshots
def create_placeholder_screenshots(project_path: Path, state: ProjectState) -> bool:
    screenshots_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(screenshots_dir)

    # Create a README with instructions
    scenarios = generate_screenshot_scenarios(project_path, state)

    readme_content = f"""# Screenshots for {state.app_name}

## Required Sizes
- iPhone 6.7" (1290 x 2796): iPhone 16 Pro Max
- iPhone 6.5" (1284 x 2778): iPhone 16 Plus
- iPad 12.9" (2048 x 2732): iPad Pro 13"
- iPad 11" (1668 x 2388): iPad Pro 11"

## Suggested Scenarios
"""
    for i, scenario in enumerate(scenarios, 1):
        readme_content += f"{i}. {scenario}\n"

    readme_content += """
## File Naming
Name files like: `01_MainScreen_iphone67.png`, `02_Feature_ipad129.png`

## Auto-generation
To auto-generate screenshots, add UI tests and run:
```bash
fastlane snapshot
```
"""
    write_file(screenshots_dir / "README.md", readme_content)

    # Note: scenarios are already cached in state.metadata by analyze_screenshot_scenarios
    print_success("Screenshot guide created in fastlane/screenshots/")

    return True


# ##################################################################
# run screenshots step
# run screenshots step with strategy: check for existing screenshots and duplicates,
# use claude agent sdk to generate and run ui tests, for widget apps explicitly capture
# widget screenshots, remove duplicate/similar screenshots, fall back to existing ui tests
# if agent fails, fall back to fastlane snapshot if available, create placeholder instructions
# as last resort
def run(project_path: Path, state: ProjectState) -> bool:
    screenshots_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(screenshots_dir)

    # Detect if this is a widget app
    is_widget_app = "widget" in state.app_name.lower() or "widget" in (state.app_description or "").lower()

    # Check if we already have screenshots
    existing_screenshots = list(screenshots_dir.glob("*.png")) + list(screenshots_dir.glob("*.jpg"))
    if existing_screenshots:
        print_info(f"Found {len(existing_screenshots)} existing screenshots")

        # Check for and remove duplicates
        removed = remove_duplicate_screenshots(screenshots_dir)

        # For widget apps, check if we have a widget screenshot
        if is_widget_app:
            remaining = list(screenshots_dir.glob("*.png"))
            has_widget_shot = any("widget" in s.name.lower() for s in remaining)
            if not has_widget_shot:
                print_warning("Widget app but no widget screenshot found - will attempt to capture one")
            else:
                # We have screenshots including widget, we're good
                return True

        # If we still have enough unique screenshots, we're done
        remaining = list(screenshots_dir.glob("*.png"))
        if len(remaining) >= 3 and not is_widget_app:
            return True

        # Need more screenshots or widget shots
        if removed > 0:
            print_info("Need to regenerate screenshots after removing duplicates")

    # Try using Claude Agent SDK to generate and run screenshot tests
    print_info("Attempting to generate screenshots with Claude Agent SDK...")
    if generate_screenshot_tests_with_agent(project_path, state):
        screenshots = list(screenshots_dir.glob("*.png"))
        if screenshots:
            print_success(f"Generated {len(screenshots)} screenshots via Claude Agent SDK")
            # Remove any duplicates
            remove_duplicate_screenshots(screenshots_dir)

    # For widget apps, explicitly try to capture widget screenshot via harness
    # But skip if app already has built-in widget preview (most widget apps display
    # their widget preview in the main UI, so existing screenshots already show it)
    if is_widget_app:
        existing = list(screenshots_dir.glob("*.png"))
        has_widget_shot = any("widget" in s.name.lower() for s in existing)

        # Check if app has built-in widget preview (in which case screenshots already show widget)
        widget_info = detect_widget_extension(project_path)
        has_builtin_preview = widget_info.get("preview_view") is not None

        if has_builtin_preview and len(existing) >= 2:
            # App has built-in preview and we have screenshots - widget is likely visible
            print_info("App has built-in widget preview - existing screenshots should show widget")
        elif not has_widget_shot:
            print_info("Attempting to capture widget screenshot via harness...")
            if capture_widget_via_harness(project_path, state, screenshots_dir):
                print_success("Widget screenshot captured successfully")
            else:
                print_warning("Could not capture widget screenshot automatically")

    # Check current state
    screenshots = list(screenshots_dir.glob("*.png"))
    if screenshots:
        # Final duplicate check
        remove_duplicate_screenshots(screenshots_dir)
        screenshots = list(screenshots_dir.glob("*.png"))
        # Widget apps with built-in preview need fewer screenshots (UI shows widget)
        min_screenshots = 2 if is_widget_app else 3
        if len(screenshots) >= min_screenshots:
            print_success(f"Have {len(screenshots)} unique screenshots")
            return True

    # Fall back: Try to find and run existing UI tests
    has_ui_tests, target_name = find_ui_test_target(project_path, state)
    if has_ui_tests:
        print_info(f"Falling back to existing UI test target: {target_name}")
        if run_ui_tests_for_screenshots(project_path, state, target_name):
            screenshots = list(screenshots_dir.glob("*.png"))
            if screenshots:
                remove_duplicate_screenshots(screenshots_dir)
                return True

    # Fall back: Try fastlane snapshot
    snapfile = project_path / "fastlane" / "Snapfile"
    if file_exists(snapfile):
        print_info("Falling back to fastlane snapshot...")
        if run_fastlane_snapshot(project_path):
            screenshots = list(screenshots_dir.glob("*.png"))
            if screenshots:
                print_success(f"Captured {len(screenshots)} screenshots via fastlane")
                remove_duplicate_screenshots(screenshots_dir)
                return True

    # Last resort: Create placeholder instructions
    create_placeholder_screenshots(project_path, state)

    # Final check
    screenshots = list(screenshots_dir.glob("*.png")) + list(screenshots_dir.glob("*.jpg"))
    if screenshots:
        print_success(f"Found {len(screenshots)} screenshots")
    else:
        print_warning("No screenshots generated - add them manually to fastlane/screenshots/en-US/")

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python screenshots.py <project_path>")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    state = load_state(project_path)

    if not state.bundle_id:
        print_error("No bundle_id in state - run structure step first")
        sys.exit(1)

    success = run(project_path, state)
    if success:
        save_state(project_path, state)
        print_success("Screenshots step completed successfully!")
    else:
        print_error("Screenshots step failed!")
        sys.exit(1)
