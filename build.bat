@echo off
echo Instalando dependencias...
pip install -r requirements.txt
echo Compilando ejecutable Alpha 8 con PyInstaller...
pyinstaller --noconsole --onefile --name "alpha8" main.py
echo Proceso finalizado. El ejecutable se encuentra en dist\alpha8.exe
pause
