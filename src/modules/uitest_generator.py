import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    ensure_dir,
    llm_json,
    llm_chat,
    file_exists,
)


# Required App Store screenshot devices
SCREENSHOT_DEVICES = [
    {"name": "iPhone 16 Pro Max", "runtime": "iOS 18.2"},
    {"name": "iPhone 16 Plus", "runtime": "iOS 18.2"},
    {"name": "iPad Pro 13-inch (M4)", "runtime": "iOS 18.2"},
    {"name": "iPad Pro 11-inch (M4)", "runtime": "iOS 18.2"},
]


# ##################################################################
# analyze app for tests
# use llm to analyze the app's source code and identify available screens/phases,
# navigation patterns, key ui elements for testing, and screenshot scenarios
def analyze_app_for_tests(project_path: Path) -> dict[str, any]:
    print_info("Analyzing app structure for UI tests...")

    # Find relevant source files
    source_files = []
    for pattern in ["*.js", "*.ts", "*.jsx", "*.tsx", "*.html", "*.swift"]:
        for f in project_path.glob(f"**/{pattern}"):
            if "node_modules" not in str(f) and "build" not in str(f):
                if f.stat().st_size < 100000:
                    source_files.append(f)

    # Read key files (limit to manageable size)
    code_snippets = []
    total_size = 0
    max_size = 80000  # 80KB limit for analysis

    priority_files = ["ui.js", "app.js", "index.js", "main.js", "index.html", "ViewController.swift"]

    # Sort files with priority ones first
    source_files.sort(key=lambda f: (
        0 if f.name in priority_files else 1,
        f.stat().st_size
    ))

    for f in source_files:
        if total_size > max_size:
            break
        try:
            content = f.read_text()
            if len(content) + total_size < max_size:
                code_snippets.append(f"=== {f.name} ===\n{content[:15000]}")
                total_size += len(content)
        except:
            pass

    if not code_snippets:
        print_warning("No analyzable source files found")
        return get_default_analysis()

    # Use LLM to analyze
    analysis_prompt = f"""Analyze this app's source code to create UI test scenarios for App Store screenshots.

Identify:
1. All distinct screens/views/phases in the app
2. How to navigate between them (element IDs, accessibility identifiers, gestures)
3. What actions trigger state changes
4. What scenarios would make compelling App Store screenshots

For a Capacitor/web app, the UI tests will interact via accessibility identifiers or tap coordinates.
Look for:
- Screen IDs (getElementById patterns)
- Phase/state variables that control what's shown
- Button click handlers
- Navigation logic

Source code:
{chr(10).join(code_snippets[:8])}

Respond with JSON in this exact format:
{{
    "app_type": "game|utility|social|productivity|etc",
    "app_description": "Brief description of what the app does",
    "screens": [
        {{
            "name": "screen_id",
            "description": "What this screen shows",
            "accessibility_id": "element ID or accessibility identifier to verify this screen",
            "trigger": "How to reach this screen from previous"
        }}
    ],
    "test_scenarios": [
        {{
            "name": "scenario_name",
            "description": "What the screenshot will show (for App Store listing)",
            "screenshot_name": "01_title",
            "screen": "which_screen",
            "setup_steps": [
                {{
                    "action": "tap|swipe|wait|type",
                    "target": "accessibility_id or description",
                    "value": "optional value for type action or wait duration"
                }}
            ],
            "priority": 1,
            "caption_suggestion": "Suggested caption for this screenshot in App Store"
        }}
    ]
}}

Focus on 3-6 key scenarios that would look good as App Store screenshots.
Prioritize: main functionality, key features, visual appeal.
"""

    result = llm_json(analysis_prompt)

    if result and result.get("test_scenarios"):
        print_success(f"Found {len(result.get('screens', []))} screens, {len(result.get('test_scenarios', []))} test scenarios")
        return result
    else:
        print_warning("LLM analysis incomplete, using default scenarios")
        return get_default_analysis()


# ##################################################################
# get default analysis
# return default analysis for apps that can't be analyzed
def get_default_analysis() -> dict[str, any]:
    return {
        "app_type": "unknown",
        "app_description": "Application",
        "screens": [
            {"name": "main", "description": "Main screen", "accessibility_id": "main-view", "trigger": "app launch"}
        ],
        "test_scenarios": [
            {
                "name": "main_screen",
                "description": "Main app screen",
                "screenshot_name": "01_main",
                "screen": "main",
                "setup_steps": [{"action": "wait", "target": "", "value": "2"}],
                "priority": 1,
                "caption_suggestion": "Welcome to the app"
            }
        ]
    }


# ##################################################################
# generate uitest swift code
# generate swift ui test code based on the analysis
# for capacitor webview apps, uses coordinate-based taps on the webview
def generate_uitest_swift_code(analysis: dict[str, any], bundle_id: str) -> str:
    scenarios = analysis.get("test_scenarios", [])
    app_description = analysis.get("app_description", "Application")

    # Generate test methods
    test_methods = []

    for scenario in sorted(scenarios, key=lambda s: s.get("priority", 5)):
        name = scenario.get("name", "unknown").replace(" ", "_").replace("-", "_")
        method_name = f"test{name.title().replace('_', '')}"
        description = scenario.get("description", "")
        screenshot_name = scenario.get("screenshot_name", name)
        steps = scenario.get("setup_steps", [])

        # Generate step code
        step_code = []
        step_num = 0
        for step in steps:
            action = step.get("action", "wait")
            target = step.get("target", "")
            value = step.get("value", "")

            if action == "tap":
                step_num += 1
                step_code.append(f'        // Tap on {target or "screen"}')
                step_code.append(f'        let webView = app.webViews.firstMatch')
                step_code.append(f'        if webView.waitForExistence(timeout: 5) {{')
                step_code.append(f'            webView.tap()')
                step_code.append(f'        }}')
                step_code.append(f'        Thread.sleep(forTimeInterval: 0.5)')
            elif action == "wait":
                # Convert milliseconds to seconds
                wait_ms = float(value) if value else 2000
                wait_sec = wait_ms / 1000.0 if wait_ms > 100 else wait_ms
                step_code.append(f'        // Wait for UI to settle')
                step_code.append(f'        Thread.sleep(forTimeInterval: {wait_sec})')
            elif action == "type":
                step_code.append(f'        // Type text: {value}')
                if target:
                    step_code.append(f'        let textField = app.textFields["{target}"]')
                    step_code.append(f'        if textField.waitForExistence(timeout: 5) {{')
                    step_code.append(f'            textField.tap()')
                    step_code.append(f'            textField.typeText("{value}")')
                    step_code.append(f'        }}')
            elif action == "swipe":
                direction = value or "up"
                step_code.append(f'        // Swipe {direction}')
                step_code.append(f'        app.swipe{direction.title()}()')

        steps_str = "\n".join(step_code) if step_code else "        // No setup steps needed\n        Thread.sleep(forTimeInterval: 1)"

        test_method = f'''
    /// {description}
    func {method_name}() {{
        let app = XCUIApplication()

{steps_str}

        // Wait for any animations
        Thread.sleep(forTimeInterval: 1)

        // Take screenshot
        snapshot("{screenshot_name}")
    }}
'''
        test_methods.append(test_method)

    # Build complete test file
    swift_code = f'''//
//  ScreenshotUITests.swift
//  AppUITests
//
//  Auto-generated UI tests for App Store screenshots
//  App: {app_description}
//

import XCTest

class ScreenshotUITests: XCTestCase {{

    override func setUpWithError() throws {{
        continueAfterFailure = false

        let app = XCUIApplication()
        setupSnapshot(app)
        app.launch()

        // Wait for app to fully load
        Thread.sleep(forTimeInterval: 3)
    }}

    override func tearDownWithError() throws {{
        // Cleanup after each test
    }}
{"".join(test_methods)}
}}
'''
    return swift_code


# ##################################################################
# generate snapfile
# generate fastlane snapfile configuration
def generate_snapfile(project_path: Path, devices: list[dict]) -> str:
    device_list = ", ".join([f'"{d["name"]}"' for d in devices])

    snapfile = f'''# Snapfile - Auto-generated for screenshot capture

# Devices to capture screenshots on
devices([
  {device_list}
])

# Languages
languages([
  "en-US"
])

# Xcode project
project("./ios/App/App.xcodeproj")

# Scheme containing UI tests
scheme("AppUITests")

# Output directory
output_directory("./fastlane/screenshots")

# Clear previous screenshots
clear_previous_screenshots(true)

# Skip cleaning between builds
skip_open_summary(true)

# Concurrent simulators
concurrent_simulators(false)
'''
    return snapfile


# ##################################################################
# create uitest target
# create the ui test target in the xcode project
# this modifies the project.pbxproj to add the test target
def create_uitest_target(project_path: Path) -> bool:
    print_info("Creating UI Test target...")

    ios_path = project_path / "ios" / "App"
    project_file = ios_path / "App.xcodeproj" / "project.pbxproj"

    if not file_exists(project_file):
        print_error(f"Project file not found: {project_file}")
        return False

    # Create test directory
    test_dir = ios_path / "AppUITests"
    ensure_dir(test_dir)

    # Check if UI test target already exists
    content = project_file.read_text()
    if "AppUITests" in content:
        print_info("UI Test target already exists")
        return True

    # We'll use xcodebuild to add the target via a Ruby script with xcodeproj gem
    # For now, create the test files and provide instructions

    print_warning("UI Test target needs to be added to Xcode project")
    print_info("Creating test files in AppUITests/")

    return True


# ##################################################################
# create uitest files
# create all necessary ui test files
def create_uitest_files(project_path: Path, analysis: dict[str, any], bundle_id: str) -> bool:
    print_info("Creating UI test files...")

    ios_path = project_path / "ios" / "App"
    test_dir = ios_path / "AppUITests"
    ensure_dir(test_dir)

    # Generate Swift test code
    swift_code = generate_uitest_swift_code(analysis, bundle_id)
    test_file = test_dir / "ScreenshotUITests.swift"
    test_file.write_text(swift_code)
    print_success(f"Created: {test_file.name}")

    # Create Info.plist for test target
    info_plist = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>$(DEVELOPMENT_LANGUAGE)</string>
    <key>CFBundleExecutable</key>
    <string>$(EXECUTABLE_NAME)</string>
    <key>CFBundleIdentifier</key>
    <string>$(PRODUCT_BUNDLE_IDENTIFIER)</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>$(PRODUCT_NAME)</string>
    <key>CFBundlePackageType</key>
    <string>$(PRODUCT_BUNDLE_PACKAGE_TYPE)</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
</dict>
</plist>
'''
    plist_file = test_dir / "Info.plist"
    plist_file.write_text(info_plist)
    print_success(f"Created: {plist_file.name}")

    # Create SnapshotHelper.swift (from fastlane)
    snapshot_helper = '''//
//  SnapshotHelper.swift
//  Example
//
//  Created by Felix Krause on 10/8/15.
//

import Foundation
import XCTest

var deviceLanguage = ""
var locale = ""

func setupSnapshot(_ app: XCUIApplication, waitForAnimations: Bool = true) {
    Snapshot.setupSnapshot(app, waitForAnimations: waitForAnimations)
}

func snapshot(_ name: String, waitForLoadingIndicator: Bool = true) {
    if waitForLoadingIndicator {
        Snapshot.snapshot(name, timeWaitingForIdle: 10)
    } else {
        Snapshot.snapshot(name, timeWaitingForIdle: 0)
    }
}

enum SnapshotError: Error, CustomDebugStringConvertible {
    case cannotFindSimulatorHomeDirectory
    case cannotRunOnPhysicalDevice

    var debugDescription: String {
        switch self {
        case .cannotFindSimulatorHomeDirectory:
            return "Couldn't find simulator home directory"
        case .cannotRunOnPhysicalDevice:
            return "Running on physical device is not supported"
        }
    }
}

@objcMembers
open class Snapshot: NSObject {
    static var app: XCUIApplication?
    static var waitForAnimations = true
    static var cacheDirectory: URL?
    static var screenshotsDirectory: URL? {
        return cacheDirectory?.appendingPathComponent("screenshots", isDirectory: true)
    }

    open class func setupSnapshot(_ app: XCUIApplication, waitForAnimations: Bool = true) {
        Snapshot.app = app
        Snapshot.waitForAnimations = waitForAnimations

        do {
            let cacheDir = try getCacheDirectory()
            Snapshot.cacheDirectory = cacheDir
            setLanguage(app)
            setLocale(app)
            setLaunchArguments(app)
        } catch {
            NSLog("Snapshot error: \\(error)")
        }
    }

    class func setLanguage(_ app: XCUIApplication) {
        guard let cacheDirectory = cacheDirectory else {
            return
        }

        let path = cacheDirectory.appendingPathComponent("language.txt")
        do {
            let trimCharacterSet = CharacterSet.whitespacesAndNewlines
            deviceLanguage = try String(contentsOf: path, encoding: .utf8).trimmingCharacters(in: trimCharacterSet)
            app.launchArguments += ["-AppleLanguages", "(\\(deviceLanguage))"]
        } catch {
            NSLog("Couldn't detect language")
        }
    }

    class func setLocale(_ app: XCUIApplication) {
        guard let cacheDirectory = cacheDirectory else {
            return
        }

        let path = cacheDirectory.appendingPathComponent("locale.txt")
        do {
            let trimCharacterSet = CharacterSet.whitespacesAndNewlines
            locale = try String(contentsOf: path, encoding: .utf8).trimmingCharacters(in: trimCharacterSet)
        } catch {
            NSLog("Couldn't detect locale")
        }

        if locale.isEmpty, !deviceLanguage.isEmpty {
            locale = Locale(identifier: deviceLanguage).identifier
        }

        if !locale.isEmpty {
            app.launchArguments += ["-AppleLocale", locale]
        }
    }

    class func setLaunchArguments(_ app: XCUIApplication) {
        guard let cacheDirectory = cacheDirectory else {
            return
        }

        let path = cacheDirectory.appendingPathComponent("snapshot-launch_arguments.txt")
        app.launchArguments += ["-FASTLANE_SNAPSHOT", "YES", "-ui_testing"]

        do {
            let argsString = try String(contentsOf: path, encoding: .utf8)
            let launchArgs = argsString.components(separatedBy: .newlines)
            for arg in launchArgs {
                let trimmed = arg.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty {
                    app.launchArguments.append(trimmed)
                }
            }
        } catch {
            // No launch arguments file
        }
    }

    open class func snapshot(_ name: String, timeWaitingForIdle timeout: TimeInterval = 10) {
        guard let app = app else {
            NSLog("app not set, call setupSnapshot first")
            return
        }

        let screenshot = app.windows.firstMatch.screenshot()
        guard var screenshotsDir = screenshotsDirectory else {
            NSLog("Couldn't find screenshots directory")
            return
        }

        do {
            try FileManager.default.createDirectory(at: screenshotsDir, withIntermediateDirectories: true, attributes: nil)
        } catch {
            NSLog("Error creating screenshots directory: \\(error)")
            return
        }

        screenshotsDir.appendPathComponent("\\(name).png")
        do {
            try screenshot.pngRepresentation.write(to: screenshotsDir)
        } catch {
            NSLog("Error writing screenshot: \\(error)")
        }
    }

    class func getCacheDirectory() throws -> URL {
        guard let simulatorHostHome = ProcessInfo.processInfo.environment["SIMULATOR_HOST_HOME"] else {
            throw SnapshotError.cannotFindSimulatorHomeDirectory
        }

        let homeDir = URL(fileURLWithPath: simulatorHostHome)
        return homeDir.appendingPathComponent("Library/Caches/tools.fastlane")
    }
}
'''
    helper_file = test_dir / "SnapshotHelper.swift"
    helper_file.write_text(snapshot_helper)
    print_success(f"Created: {helper_file.name}")

    # Update Snapfile
    fastlane_dir = project_path / "fastlane"
    ensure_dir(fastlane_dir)
    snapfile_content = generate_snapfile(project_path, SCREENSHOT_DEVICES)
    snapfile_path = fastlane_dir / "Snapfile"
    snapfile_path.write_text(snapfile_content)
    print_success(f"Updated: {snapfile_path.name}")

    return True


# ##################################################################
# add uitest target to project
# add the ui test target to the xcode project using xcodeproj gem
def add_uitest_target_to_project(project_path: Path, bundle_id: str) -> bool:
    print_info("Adding UI Test target to Xcode project...")

    ios_path = project_path / "ios" / "App"
    test_dir = ios_path / "AppUITests"

    # Create a Ruby script to add the target
    ruby_script = f'''
require 'xcodeproj'

project_path = '{ios_path}/App.xcodeproj'
project = Xcodeproj::Project.open(project_path)

# Check if target already exists
if project.targets.any? {{ |t| t.name == 'AppUITests' }}
  puts "UI Test target already exists"
  exit 0
end

# Find the main app target
main_target = project.targets.find {{ |t| t.name == 'App' }}
unless main_target
  puts "Could not find main App target"
  exit 1
end

# Create UI Test target
test_target = project.new_target(:ui_test_bundle, 'AppUITests', :ios, '15.0')
test_target.add_dependency(main_target)

# Set bundle identifier
test_target.build_configurations.each do |config|
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = '{bundle_id}.uitests'
  config.build_settings['TEST_HOST'] = ''
  config.build_settings['TEST_TARGET_NAME'] = 'App'
  config.build_settings['INFOPLIST_FILE'] = 'AppUITests/Info.plist'
  config.build_settings['CODE_SIGN_STYLE'] = 'Automatic'
  config.build_settings['DEVELOPMENT_TEAM'] = main_target.build_configurations.first.build_settings['DEVELOPMENT_TEAM']
end

# Add source files to target
test_group = project.main_group.find_subpath('AppUITests', true)
test_group.set_source_tree('<group>')
test_group.set_path('AppUITests')

Dir.glob('{test_dir}/*.swift').each do |file|
  file_ref = test_group.new_file(File.basename(file))
  test_target.add_file_references([file_ref])
end

# Add Info.plist
test_group.new_file('Info.plist')

# Create test scheme
scheme = Xcodeproj::XCScheme.new
scheme.add_test_target(test_target)
scheme.save_as(project_path, 'AppUITests')

project.save
puts "Successfully added UI Test target"
'''

    script_path = project_path / "add_uitest_target.rb"
    script_path.write_text(ruby_script)

    # Run the Ruby script
    ret_code, output = exec_cmd(["ruby", str(script_path)], timeout=60)

    # Clean up script
    if script_path.exists():
        script_path.unlink()

    if ret_code == 0:
        print_success("UI Test target added to project")
        return True
    else:
        # If xcodeproj gem not available, provide manual instructions
        print_warning("Could not automatically add UI Test target")
        print_info("To add manually in Xcode:")
        print_info("  1. Open App.xcodeproj")
        print_info("  2. File > New > Target > UI Testing Bundle")
        print_info("  3. Name it 'AppUITests'")
        print_info("  4. Add the generated Swift files from ios/App/AppUITests/")
        return False


# ##################################################################
# run ui test generation
# main entry point for ui test generation: analyzes app structure using llm,
# generates swift ui test code, creates/updates xcode project with test target,
# and returns analysis for use by screenshot capture
def run(project_path: Path, bundle_id: str) -> dict[str, any]:
    print_info("Generating UI tests for screenshot capture...")

    # Step 1: Analyze app
    analysis = analyze_app_for_tests(project_path)

    # Save analysis for reference
    analysis_file = project_path / "fastlane" / "screenshot_analysis.json"
    ensure_dir(analysis_file.parent)
    analysis_file.write_text(json.dumps(analysis, indent=2))
    print_success(f"Saved analysis: {analysis_file.name}")

    # Step 2: Create test files
    create_uitest_files(project_path, analysis, bundle_id)

    # Step 3: Add test target to project (may require manual steps)
    create_uitest_target(project_path)
    add_uitest_target_to_project(project_path, bundle_id)

    # Summary
    scenarios = analysis.get("test_scenarios", [])
    print_success(f"Generated {len(scenarios)} UI test scenarios")
    for s in scenarios:
        print_info(f"  - {s.get('screenshot_name')}: {s.get('description', '')[:50]}")

    return analysis


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python uitest_generator.py <project_path> [bundle_id]")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2:
        bundle_id = sys.argv[2]
    else:
        print_error("Bundle ID is required")
        sys.exit(1)

    result = run(project_path, bundle_id)
    if result:
        print_success("UI test generation completed!")
    else:
        print_error("UI test generation failed!")
        sys.exit(1)
