# sorc - Linux Screen Orchestrator 🖥️

![Language](https://img.shields.io/badge/Language-Python%20%7C%20Shell-blue)
![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

**sorc** is a lightweight, scriptable orchestrator for managing GNU `screen` sessions on Linux environments. Built with Python and Shell, it provides an easy way to automate, monitor, and manage your background terminal processes and multiplexed sessions. 

## ✨ Features
* **Automated Session Management**: Easily spawn, track, and kill multiple GNU `screen` sessions.
* **Lightweight**: Minimal dependencies, running primarily on native Python and Bash.
* **Easy Installation**: One-line install script to get you up and running in seconds. 
* **CLI-Driven**: Built for developers and sysadmins who live in the terminal.

## 🚀 Installation

You can install `sorc` globally on your Linux machine using our automated installation script. 

Simply run the following command in your terminal:

```bash
curl -fsSL [https://raw.githubusercontent.com/TheDynomike/sorc/main/install.sh](https://raw.githubusercontent.com/TheDynomike/sorc/main/install.sh) | bash
```
Note: Depending on your system permissions, the installation script may prompt you for sudo access to place the executable in your system's PATH (e.g., /usr/local/bin).

## 🛠️ Prerequisites

Before installing sorc, ensure your system has the following installed:

    A Linux-based OS

    python3

    screen (GNU Screen)

    curl (for the installation script)

## 💻 Usage

(Note: Update this section with specific commands once you expand your script's CLI flags!)

Once installed, you can use the sorc command directly from your terminal.
Bash
```
# Example command structure 
sorc [options] <command>

Common operations:

    Create a new orchestrated screen: sorc start <name>

    List active orchestrated screens: sorc list

    Stop an orchestrated screen: sorc stop <name>
```
## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

    Fork the Project

    Create your Feature Branch (git checkout -b feature/AmazingFeature)

    Commit your Changes (git commit -m 'Add some AmazingFeature')

    Push to the Branch (git push origin feature/AmazingFeature)

    Open a Pull Request

## 📝 License

Distributed under the MIT License. See LICENSE for more information.

Created by TheDynomike
