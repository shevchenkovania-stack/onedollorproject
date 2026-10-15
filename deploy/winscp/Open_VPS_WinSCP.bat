@echo off
rem WinSCP launcher for the ChirieBot VPS (SFTP over the existing SSH port 22).
rem Opens WinSCP logged-in target dir; it will prompt for the password once,
rem tick "Save password" and future double-clicks connect instantly.
rem Password is NOT stored in this file.
title WinSCP - VPS imobiliare (SFTP)
start "" "C:\Users\isevcenco\AppData\Local\Programs\WinSCP\WinSCP.exe" "sftp://root@89.40.3.91/opt/imobiliare/test_data/images/"
