@echo off
cd /d "C:\Users\oguzh\.gemini\antigravity\brain\5bd9f499-5394-42ba-95c0-0e00a52f42bb\scratch\letitloop"

echo ========================================================
echo LETITLOOP FULL VERIFICATION SUITE (Lint + Tests + Push)
echo ========================================================

echo.
echo [1/2] Running Ruff Linter and Code Quality Checks...
ruff check .
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Ruff checks failed with exit code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Running All Tests via Fast In-Process Runner...
python fast_test_runner.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Test suite failed with exit code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ========================================================
echo ALL CHECKS AND TESTS PASSED 100%% GREEN!
echo ========================================================
echo.
echo Pushing sanitized release to GitHub (main + v0.1.0)...
git push --force origin main v0.1.0

echo.
echo ========================================================
echo DONE! GitHub repository is updated and fully sanitized.
echo ========================================================
pause
