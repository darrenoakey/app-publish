import json
import time
import subprocess
from pathlib import Path
import re

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
)

# Required App Store screenshot devices
SCREENSHOT_DEVICES = [
    {"name": "iPhone 16 Pro Max", "suffix": "iphone67", "display": "6.7 inch"},
    {"name": "iPhone 16 Plus", "suffix": "iphone65", "display": "6.5 inch"},
    {"name": "iPad Pro 13-inch (M4)", "suffix": "ipad129", "display": "12.9 inch"},
    {"name": "iPad Pro 11-inch (M4)", "suffix": "ipad11", "display": "11 inch"},
]


# ##################################################################
# analyze app structure
# use llm to analyze the app's source code and identify available screens/phases,
# navigation patterns, key ui elements, and screenshot opportunities
def analyze_app_structure(project_path: Path) -> dict[str, any]:
    print_info("Analyzing app structure...")

    # Find relevant source files
    source_files = []
    for pattern in ["*.js", "*.ts", "*.jsx", "*.tsx", "*.html"]:
        for f in project_path.glob(pattern):
            if "node_modules" not in str(f) and f.stat().st_size < 100000:
                source_files.append(f)

    # Read key files (limit to manageable size)
    code_snippets = []
    total_size = 0
    max_size = 50000  # 50KB limit for analysis

    priority_files = ["ui.js", "app.js", "index.js", "main.js", "index.html"]

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
                code_snippets.append(f"=== {f.name} ===\n{content[:10000]}")
                total_size += len(content)
        except:
            pass

    if not code_snippets:
        print_warning("No analyzable source files found")
        return {"screens": [], "navigation": [], "elements": []}

    # Use LLM to analyze
    analysis_prompt = f"""Analyze this app's source code and identify:

1. All distinct screens/views/phases in the app
2. How to navigate between them (button IDs, function calls, etc.)
3. Key UI elements that would look good in screenshots
4. What scenarios would make compelling App Store screenshots

Source code:
{chr(10).join(code_snippets[:5])}

Respond with JSON in this format:
{{
    "app_type": "game|utility|social|etc",
    "screens": [
        {{"name": "screen_name", "description": "what it shows", "trigger": "how to get there"}}
    ],
    "navigation": [
        {{"from": "screen1", "to": "screen2", "action": "click #button-id or call function()"}}
    ],
    "screenshot_scenarios": [
        {{
            "name": "scenario_name",
            "description": "what the screenshot shows",
            "screen": "which_screen",
            "setup_steps": ["step1", "step2"],
            "priority": 1
        }}
    ]
}}
"""

    result = llm_json(analysis_prompt)

    if result:
        print_success(f"Found {len(result.get('screens', []))} screens, {len(result.get('screenshot_scenarios', []))} scenarios")
        return result
    else:
        print_warning("LLM analysis failed, using default scenarios")
        return {
            "screens": [{"name": "main", "description": "Main screen", "trigger": "app launch"}],
            "screenshot_scenarios": [
                {"name": "main", "description": "Main app screen", "screen": "main", "setup_steps": [], "priority": 1}
            ]
        }


# ##################################################################
# generate automation script
# generate a javascript automation script based on the analysis
# this script will be injected into the app to control navigation
def generate_automation_script(analysis: dict, project_path: Path) -> Path:
    print_info("Generating automation script...")

    scenarios = analysis.get("screenshot_scenarios", [])
    navigation = analysis.get("navigation", [])

    # Build navigation map
    nav_map = {}
    for nav in navigation:
        key = f"{nav.get('from', 'start')}_to_{nav.get('to', 'unknown')}"
        nav_map[key] = nav.get("action", "")

    # Generate JavaScript
    script = """// Auto-generated screenshot automation script
(function() {
    'use strict';

    const STEP_DELAY = 2000; // ms between steps
    let currentStep = 0;
    let screenshotCallback = null;

    const scenarios = %s;

    const navigationActions = %s;

    function executeAction(action) {
        if (!action) return;

        // Handle different action types
        if (action.startsWith('click ')) {
            const selector = action.replace('click ', '');
            const el = document.querySelector(selector);
            if (el) el.click();
        } else if (action.startsWith('call ')) {
            try {
                eval(action.replace('call ', ''));
            } catch(e) {
                console.error('[Screenshot] Action failed:', e);
            }
        } else if (action.includes('.click()')) {
            try {
                eval(action);
            } catch(e) {
                console.error('[Screenshot] Action failed:', e);
            }
        }
    }

    function runScenario(index) {
        if (index >= scenarios.length) {
            console.log('[Screenshot] All scenarios complete');
            return;
        }

        const scenario = scenarios[index];
        console.log('[Screenshot] Running scenario:', scenario.name);

        // Execute setup steps
        const steps = scenario.setup_steps || [];
        let stepIndex = 0;

        function runNextStep() {
            if (stepIndex < steps.length) {
                executeAction(steps[stepIndex]);
                stepIndex++;
                setTimeout(runNextStep, STEP_DELAY);
            } else {
                // Ready for screenshot
                console.log('[Screenshot] READY:', scenario.name);
                window.postMessage({type: 'screenshot_ready', scenario: scenario.name}, '*');

                // Move to next scenario after delay
                setTimeout(() => runScenario(index + 1), STEP_DELAY * 2);
            }
        }

        runNextStep();
    }

    // Expose global function to start automation
    window.startScreenshotAutomation = function(callback) {
        screenshotCallback = callback;
        console.log('[Screenshot] Starting automation with', scenarios.length, 'scenarios');
        runScenario(0);
    };

    window.getCurrentScenario = function() {
        return scenarios[currentStep] || null;
    };

    console.log('[Screenshot] Automation script loaded.', scenarios.length, 'scenarios available.');
    console.log('[Screenshot] Call window.startScreenshotAutomation() to begin.');
})();
""" % (json.dumps(scenarios, indent=2), json.dumps(nav_map, indent=2))

    # Save script
    script_path = project_path / "screenshot-automation.js"
    script_path.write_text(script)
    print_success(f"Generated: {script_path.name}")

    return script_path


# ##################################################################
# build simulator app
# build the app for ios simulator
def build_simulator_app(project_path: Path) -> Path:
    print_info("Building app for simulator...")

    # Build command
    build_dir = Path("/tmp/screenshot-build")

    ret_code, output = exec_cmd([
        "xcodebuild",
        "-project", str(project_path / "ios" / "App" / "App.xcodeproj"),
        "-scheme", "App",
        "-sdk", "iphonesimulator",
        "-configuration", "Debug",
        "-derivedDataPath", str(build_dir),
        "build"
    ], timeout=300)

    if ret_code != 0:
        print_error(f"Build failed: {output[-500:]}")
        return None

    app_path = build_dir / "Build" / "Products" / "Debug-iphonesimulator" / "App.app"
    if app_path.exists():
        print_success("Build complete")
        return app_path

    print_error("App bundle not found after build")
    return None


# ##################################################################
# capture device screenshots
# capture screenshots on a specific device
def capture_device_screenshots(
    device: dict,
    bundle_id: str,
    app_path: Path,
    scenarios: list[dict],
    output_dir: Path
) -> int:
    device_name = device["name"]
    suffix = device["suffix"]
    captured = 0

    print_info(f"Capturing on {device_name}...")

    # Boot simulator
    exec_cmd(["xcrun", "simctl", "boot", device_name])
    time.sleep(3)

    # Install app
    exec_cmd(["xcrun", "simctl", "install", device_name, str(app_path)])

    # Launch app
    exec_cmd(["xcrun", "simctl", "launch", device_name, bundle_id])
    time.sleep(4)  # Wait for app to load

    # Capture screenshots for each priority-1 scenario
    for scenario in sorted(scenarios, key=lambda s: s.get("priority", 5)):
        if scenario.get("priority", 5) > 2:  # Only capture high priority
            continue

        name = scenario.get("name", "unknown").replace(" ", "_").lower()
        output_path = output_dir / f"{name}_{suffix}.png"

        # For now, capture current state
        # Full automation would inject JS and control navigation
        ret_code, _ = exec_cmd([
            "xcrun", "simctl", "io", device_name, "screenshot", str(output_path)
        ])

        if ret_code == 0:
            print_success(f"  Captured: {output_path.name}")
            captured += 1
        else:
            print_warning(f"  Failed: {output_path.name}")

        time.sleep(1)

    return captured


# ##################################################################
# run screenshot capture
# main entry point for screenshot capture: analyzes app structure using llm,
# generates automation scenarios, builds app for simulator, and captures
# screenshots on all required devices
def run(project_path: Path, bundle_id: str) -> bool:
    output_dir = project_path / "fastlane" / "screenshots" / "en-US"
    ensure_dir(output_dir)

    # Step 1: Analyze app
    analysis = analyze_app_structure(project_path)
    scenarios = analysis.get("screenshot_scenarios", [])

    if not scenarios:
        print_warning("No screenshot scenarios identified")
        scenarios = [{"name": "main", "description": "Main screen", "priority": 1}]

    # Step 2: Generate automation script
    script_path = generate_automation_script(analysis, project_path)

    # Step 3: Sync changes to iOS (for Capacitor apps)
    exec_cmd(["npx", "cap", "sync", "ios"], cwd=project_path)

    # Step 4: Build for simulator
    app_path = build_simulator_app(project_path)
    if not app_path:
        return False

    # Step 5: Capture on all devices
    total_captured = 0
    for device in SCREENSHOT_DEVICES:
        count = capture_device_screenshots(
            device, bundle_id, app_path, scenarios, output_dir
        )
        total_captured += count

    # Summary
    print_success(f"Captured {total_captured} screenshots")

    # List files
    screenshots = list(output_dir.glob("*.png"))
    if screenshots:
        print_info(f"Screenshots in {output_dir}:")
        for s in sorted(screenshots):
            print_info(f"  - {s.name}")

    return total_captured > 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python screenshot_agent.py <project_path> [bundle_id]")
        sys.exit(1)

    project_path = Path(sys.argv[1]).resolve()
    if len(sys.argv) > 2:
        bundle_id = sys.argv[2]
    else:
        print_error("Bundle ID is required")
        sys.exit(1)

    success = run(project_path, bundle_id)
    if success:
        print_success("Screenshot capture completed!")
    else:
        print_error("Screenshot capture failed!")
        sys.exit(1)
