![](banner.jpg)

# app-publish

Automate the complete iOS App Store publishing workflow from build to submission.

## Purpose

app-publish handles the end-to-end process of publishing iOS applications to the App Store. It manages the entire pipeline including building, code signing, screenshot generation, metadata preparation, upload, and submission.

## Installation

1. Clone or download this repository
2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Ensure you have the required iOS development tools installed (Xcode, xcrun, etc.)

## Usage

The `run` script provides all commands. Set your project directory using one of:
- The `--project` / `-p` flag
- The `APP_PUBLISH_PROJECT` environment variable
- Current working directory (default)

### Run Full Pipeline

Execute the complete publishing workflow:

```bash
./run pipeline
./run pipeline --project /path/to/ios/project
```

Or simply:

```bash
./run
```

### Check Pipeline Status

View current progress through the pipeline:

```bash
./run status
```

### Restart Pipeline

Clear state and start fresh:

```bash
./run restart
```

### Run Specific Step

Execute a single pipeline step:

```bash
./run step detect
./run step build
./run step upload
./run step submit
```

### Deploy to Device

Install the app on a connected iOS device:

```bash
./run deploy
./run deploy "My iPhone"
```

Default device name is "Starbuck".

## Development

### Run Tests

```bash
./run test src/config.py
./run test src/modules/build.py
```

### Run Linter

```bash
./run lint
```

### Run Full Quality Check

```bash
./run check
```

## Examples

Publish an app with explicit project path:

```bash
./run --project ~/Projects/MyiOSApp pipeline
```

Check status using environment variable:

```bash
export APP_PUBLISH_PROJECT=~/Projects/MyiOSApp
./run status
```

Run only the build step:

```bash
./run -p ~/Projects/MyiOSApp step build
```

Deploy to a specific device:

```bash
./run -p ~/Projects/MyiOSApp deploy "iPhone 15 Pro"
```