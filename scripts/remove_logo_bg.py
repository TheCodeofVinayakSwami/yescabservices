#!/usr/bin/env python3
"""
Simple script to convert a logo with a white background to a PNG with transparency.

Usage:
  python scripts/remove_logo_bg.py [input_path] [output_path] [threshold]

Default:
  input_path: static/img/logo.jpg
  output_path: static/img/logo.png
  threshold: 240  # pixels with RGB >= threshold treated as background (0-255)

This uses Pillow (PIL). Install with `pip install Pillow` or add to requirements.txt.
"""
import sys
from PIL import Image


def remove_white_background(input_path, output_path, threshold=240):
    img = Image.open(input_path).convert("RGBA")
    datas = img.getdata()
    new_data = []
    for item in datas:
        r, g, b, a = item
        if r >= threshold and g >= threshold and b >= threshold:
            # make pixel transparent
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append((r, g, b, a))
    img.putdata(new_data)
    img.save(output_path, "PNG")


def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else "static/img/logo.jpg"
    out = sys.argv[2] if len(sys.argv) > 2 else "static/img/logo.png"
    try:
        thr = int(sys.argv[3]) if len(sys.argv) > 3 else 240
    except ValueError:
        thr = 240

    try:
        remove_white_background(inp, out, thr)
        print(f"Saved transparent logo to: {out}")
    except FileNotFoundError:
        print(f"Input file not found: {inp}")
        sys.exit(1)
    except Exception as e:
        print("Error:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
