# PDF to JPG Converter

A clean, lightweight desktop application that converts every page of a PDF
into a high-quality JPEG image. Built with **Python**, **Tkinter** and
**PyMuPDF**.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![GUI](https://img.shields.io/badge/GUI-Tkinter-informational)
![PDF](https://img.shields.io/badge/PDF-PyMuPDF-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## Features

- Convert **all pages** or a **custom page range** such as `1-3, 5, 8-10`
- Adjustable resolution from **72 to 600 DPI** (default: **300 DPI**)
- Adjustable **JPEG quality** from 1 to 100 (default: **92**)
- Non-blocking conversion with **live progress**, **log** and **cancel** support
- Supports **password-protected PDFs**
- Automatically creates the output folder
- Safe, zero-padded file names: `document_page_0001.jpg`
- Cross-platform: **Windows, macOS and Linux**
- No external binaries required — no Ghostscript, Poppler or ImageMagick

---

## Screenshot

_Add a screenshot of the application here._

---

## Requirements

- Python 3.9 or newer
- PyMuPDF

---

## Installation

```bash
git clone https://github.com/your-username/pdf-to-jpg-converter.git
cd pdf-to-jpg-converter

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt