JCK MOTORSHOP POS - SERVICE / PARTS & MATERIALS WORKFLOW

OPTION A - RUN AS A PYTHON SCRIPT (needs Python installed)
1. Install Python 3.11+ and matplotlib.
2. Double-click START_POS.bat or run: python jck_motorshop_pos.py

OPTION B - BUILD A STANDALONE .EXE (no Python needed on the other laptop)
1. On THIS computer (the one with Python/VS Code), double-click BUILD_EXE.bat.
   This installs PyInstaller and builds "JCK Motorshop POS.exe" inside a
   new "dist" folder.
2. Copy these 4 files together into one folder on the OTHER laptop:
     - dist\JCK Motorshop POS.exe
     - jck_motorshop_pos.db
     - jck_logo.ico
     - jck_logo.png
3. On the other laptop, just double-click "JCK Motorshop POS.exe".
   No Python or VS Code required.

NOTE: PyInstaller only builds an exe for the operating system it runs on.
Build it on a Windows PC to get a Windows .exe.

APP ICON
- jck_logo.ico and jck_logo.png must stay in the same folder as the
  .exe (or the .py script). The app loads whichever one it finds to set
  the window/taskbar icon. If both are missing, the app still runs fine
  with the default icon.

NEW SALES WORKFLOW
- LEFT SIDE: Parts / Materials only.
- RIGHT SIDE: Services / Add-ons only.
- Select a part/material to see applicable services.
- When adding a product, the POS automatically asks which optional services the customer wants.
- Applied services stay in the Services box and are NOT mixed into the Parts/Materials cart.
- Checkout total includes both.
- Only Parts/Materials reduce inventory; Services never reduce stock.

SERVICE RULES
Inventory -> SERVICE RULES lets you configure which services apply to which parts/materials.
