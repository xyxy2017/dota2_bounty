# Bundled Tesseract

Place the packaged OCR runtime here for release builds:

```text
backend/tools/tesseract/tesseract.exe
backend/tools/tesseract/libtesseract-5.dll
backend/tools/tesseract/libleptonica-6.dll
backend/tools/tesseract/tessdata/eng.traineddata
backend/tools/tesseract/tessdata/chi_sim.traineddata
```

The backend checks this bundled path before environment variables, PATH, and common Windows install locations. The third-party binaries and traineddata files are intentionally gitignored; include them in the release artifact or installer instead of committing them to the repository.

Run this before publishing a release build:

```powershell
.\backend\scripts\check_bundled_tesseract.ps1
```
