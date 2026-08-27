# C2K FPS Perception Test

A blind FPS comparison tool for Counter-Strike 2 that I built to test whether different FPS levels actually feel different without knowing which one is active.

You can use the **installer or standalone EXE directly** from the [Releases](https://github.com/cs2kitchen/C2K-FPS-Perception-Test/releases) page. You do not need Python, an IDE, or any coding knowledge unless you specifically want to run or modify the source code.

## Safety

I designed this tool to stay as simple as possible and avoid interacting with CS2 in ways that could be considered invasive.

The program does **not**:

* inject code into CS2
* inject or load DLLs into CS2
* read or write CS2 memory
* install a kernel driver
* modify CS2 executables
* disable VAC
* draw an in-game overlay

What it does is much simpler:

* creates and updates `placebo.cfg` inside your CS2 `game\csgo\cfg` folder
* uses normal CS2 console commands, aliases and binds
* registers normal Windows hotkeys while the program is open
* closes CS2 using normal Windows process controls when required
* launches Counter-Strike 2 through Steam
* changes the FPS limit between the values selected for the test

I cannot guarantee how Valve may treat any third-party software in the future, so use the tool at your own discretion.

If you want to be extra cautious while testing, you can also add:

```text
-insecure
```

to your CS2 launch options.

`-insecure` starts CS2 without VAC-secured matchmaking, so you will not be able to join VAC-secured servers while it is enabled. Remove it again when you want to play normally.

Valve's VAC information is available here:

https://help.steampowered.com/en/faqs/view/571A-97DA-70E9-FF74

## Commands the tool changes

The generated `placebo.cfg` only uses normal CS2 console commands and binds.

The main commands being changed or used are:

```text
fps_max
alias
bind
exec
map
```

The FPS portion of the test changes:

```text
fps_max <value>
```

For example:

```text
fps_max 60
fps_max 120
fps_max 240
fps_max 0
```

`fps_max 0` means **Uncapped**. Capped choices are shown as `fps_max 64`, `fps_max 144`, and so on.

The CFG also creates aliases and binds used by the blind test so the program can switch between the RED and BLUE FPS values without telling you which FPS value is currently active.

You can open `placebo.cfg` yourself at any time and inspect exactly what is being executed.

The file is located at:

```text
Counter-Strike Global Offensive\game\csgo\cfg\placebo.cfg
```

The program does not hide the CFG or its commands.

## Easiest way to use it

Go to the [**Releases**](https://github.com/cs2kitchen/C2K-FPS-Perception-Test/releases) section of this GitHub repository.

You can either:

* run the Windows installer, or
* download the portable version and run the EXE directly

Both versions are self-contained.

You do **not** need to install:

* Python
* pip
* Visual Studio
* VS Code
* any Python packages

Just install/run the program and complete the Setup page.

## Setup

1. Install the program or run the portable EXE.
2. Open **Setup**.
3. Select your CS2 installation if it was not detected automatically.
4. The program will locate the CS2 CFG directory.

The destination should look similar to:

```text
C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\cfg
```

If your Steam library is on another drive, select that installation instead.

The app then creates:

```text
placebo.cfg
```

inside the selected CFG folder.

## CS2 launch options

Open:

```text
Steam
→ Library
→ Counter-Strike 2
→ Properties
→ General
→ Launch Options
```

Add:

```text
+exec placebo
```

If you want to run the test with VAC disabled as an additional precaution, you can instead use:

```text
-insecure +exec placebo
```

Again, `-insecure` prevents you from joining VAC-secured servers until you remove it.

## Running a test

After Setup is complete:

1. Start the application.
2. Select the FPS values you want to compare.
3. Configure the number of trials and other test options.
4. Press **F1** to start the test.
5. Play normally and decide which side feels better.
6. Press **L** to switch between **RED** and **BLUE**.
7. Press **F2** to submit your choice.
8. Press **F4** to pause the test when required.

The whole point is that you do not know which FPS value RED or BLUE represents while testing.

This makes it much harder for expectation or placebo to influence the result.

## Results

Results are saved as JSON files.

By default they are stored in:

```text
%USERPROFILE%\Documents\C2K FPS Perception Test\Results
```

Each result filename contains the test name and timestamp.

The History page can also read older result formats created by previous versions of the program.

## Running from source

You only need this section if you want to modify the program or inspect the Python source yourself.

Python 3.12 or newer is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

Normal users can ignore this entire section and simply use the installer or EXE from [Releases](https://github.com/cs2kitchen/C2K-FPS-Perception-Test/releases).

## Building the Windows version

The easiest way to build every available Windows package is:

```powershell
.\build_release.bat
```

The script creates a local `.venv`, installs the pinned build requirements, and builds as much as the available toolchain supports. Output is written to `dist`:

```text
C2K FPS Perception Test.exe
C2K FPS Perception Test Portable.zip
C2K FPS Perception Test Setup.exe
```

The standalone EXE and portable ZIP do not require Python on the target computer. Inno Setup 6 is required only for `C2K FPS Perception Test Setup.exe`; if it is unavailable, the script still builds the standalone outputs and explains how to enable the installer build.

You can also run the PowerShell build directly:

```powershell
.\build\build.ps1
```

The GitHub Actions build can handle this automatically for [releases](https://github.com/cs2kitchen/C2K-FPS-Perception-Test/releases).


