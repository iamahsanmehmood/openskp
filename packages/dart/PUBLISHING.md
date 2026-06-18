# Publishing to pub.dev

This document outlines the steps and prerequisites required to publish the `openskp` Dart package to [pub.dev](https://pub.dev).

---

## 1. Prerequisites

To publish a package on pub.dev, you need:

1. **A Google Account**: Used to authenticate and manage your packages.
2. **A Verified Publisher (Recommended)**: While you can publish packages under a personal Google account, it is highly recommended to publish under a verified publisher (e.g., `openskp.org` or your organization's domain). This displays a shield icon and builds trust with consumers.
   - To create a publisher, go to [pub.dev/create-publisher](https://pub.dev/create-publisher).

---

## 2. First-Time Manual Publishing (Bootstrapping)

Before setting up automated CI/CD publishing, **you must publish the first version of the package manually**. Pub.dev requires the package to exist and for your Google account to have publishing permissions before you can configure automated workflows.

### Step 2.1: Run a Dry Run
Verify that the package structure is correct and that it passes all pub.dev analysis rules:
```bash
dart pub publish --dry-run
```

### Step 2.2: Publish Manually
Run the publish command:
```bash
dart pub publish
```
1. The terminal will display a verification URL.
2. Open the URL in a web browser, log in with your Google account, and grant permission.
3. Once authorized, the CLI will complete the upload.

---

## 3. Recommended: Automated Publishing via Trusted Publishing (OIDC)

Trusted publishing uses OpenID Connect (OIDC) to securely authenticate your CI/CD environment (e.g., GitHub Actions) with pub.dev. It eliminates the need to manage, store, or rotate long-lived credentials/tokens in repository secrets.

### Step 3.1: Enable Automated Publishing on pub.dev
1. Navigate to your package page on [pub.dev](https://pub.dev) (after manual publishing is successful).
2. Go to the **Admin** tab.
3. Scroll to **Automated publishing**.
4. Check **Publish from GitHub Actions**.
5. Configure the following fields:
   - **Repository**: `iamahsanmehmood/openskp`
   - **Tag Pattern**: Define a release tag pattern (e.g., `v*` or `dart-v*`). For security, pub.dev only allows automated publishing triggered by pushing a git tag.

### Step 3.2: Configure the GitHub Actions Workflow
In your GitHub Actions workflow file (e.g., `.github/workflows/publish_dart.yml`), you must request the `id-token: write` permission to fetch the OIDC token.

Here is an example workflow:

```yaml
name: Publish Dart Package

on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+' # Matches tags like v0.1.0

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write # Required for OIDC authentication
      contents: read
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Setup Dart
        uses: dart-lang/setup-dart@v1

      - name: Install Dependencies
        run: dart pub get

      - name: Publish to pub.dev
        run: dart pub publish --force
```

---

## 4. Alternative: Publishing via Legacy Secrets (`PUB_CREDENTIALS`)

If OIDC is not supported or you prefer using standard repository secrets, you can use the credentials generated during your first manual publication.

### Step 4.1: Retrieve your Credentials File
After publishing manually, Dart stores credentials locally:
- **macOS/Linux**: `~/.config/dart/pub-credentials.json`
- **Windows**: `%APPDATA%\dart\pub-credentials.json`

Copy the contents of this JSON file.

### Step 4.2: Store as GitHub Secret
1. Navigate to your GitHub repository -> **Settings** -> **Secrets and variables** -> **Actions**.
2. Create a new repository secret named `PUB_CREDENTIALS` and paste the contents of your local `pub-credentials.json`.

### Step 4.3: Configure the GitHub Actions Workflow
Create a workflow that restores this credentials file before running `dart pub publish`:

```yaml
name: Publish Dart Package (Legacy Secrets)

on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+'

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Setup Dart
        uses: dart-lang/setup-dart@v1

      - name: Restore Credentials
        run: |
          mkdir -p ~/.config/dart
          echo '${{ secrets.PUB_CREDENTIALS }}' > ~/.config/dart/pub-credentials.json

      - name: Install Dependencies
        run: dart pub get

      - name: Publish to pub.dev
        run: dart pub publish --force
```

---

## 5. Maximizing pub.dev Package Score

To ensure the package gets a high score (140/140) and is recognized as a Flutter/Dart favorite:
- **Documentation**: Provide a detailed `README.md` with usage examples and a complete API reference.
- **File Structure**: Keep a clean structure containing:
  - `lib/` for library code.
  - `example/` containing a working demonstration.
  - `test/` for unit tests.
  - `CHANGELOG.md` detailing changes for each version.
- **Code Quality**:
  - Format your code using `dart format .`.
  - Analyze code rules using `dart analyze` and resolve any warnings.
- **Platform Support**: Ensure the code does not rely on platform-specific APIs if it's meant to be multi-platform (Android, iOS, Web, Windows, macOS, Linux).
