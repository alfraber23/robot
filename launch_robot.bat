@echo off
title Robot Digitalizador

pushd "\\192.160.106.23\Public\Analisis de cuentas\Ancts\PRUEBAS\robot_actualizado_py3"
if errorlevel 1 (
    echo No se pudo acceder a la carpeta del robot.
    echo Verifica que la red este disponible y la ruta sea correcta.
    popd
    pause
    exit /b 1
)

py main.py

echo.
echo [El robot ha terminado. Presiona cualquier tecla para cerrar esta ventana.]
popd
pause > nul