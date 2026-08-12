@echo off
setlocal
set PY=D:\rag_rerank\Python311\python.exe
set VENV=D:\rag_rerank\venv
set LOG=D:\rag_rerank\setup.log
set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

echo ==== setup start %DATE% %TIME% > "%LOG%"
if not exist "%VENV%\Scripts\python.exe" (
  "%PY%" -m venv "%VENV%" >> "%LOG%" 2>&1
)
call "%VENV%\Scripts\activate.bat"
python -m pip install -U pip -i %PIP_INDEX% >> "%LOG%" 2>&1
pip install fastapi uvicorn -i %PIP_INDEX% >> "%LOG%" 2>&1

REM Prefer CUDA torch; fallback CPU if mirror/network fails
pip install torch --index-url https://download.pytorch.org/whl/cu124 >> "%LOG%" 2>&1
if errorlevel 1 (
  echo torch cu124 failed, try cu121 >> "%LOG%"
  pip install torch --index-url https://download.pytorch.org/whl/cu121 >> "%LOG%" 2>&1
)
if errorlevel 1 (
  echo CUDA torch failed, install CPU torch from tuna >> "%LOG%"
  pip install torch -i %PIP_INDEX% >> "%LOG%" 2>&1
)

pip install FlagEmbedding -i %PIP_INDEX% >> "%LOG%" 2>&1
python -c "import torch; print('cuda', torch.cuda.is_available()); print(torch.__version__)" >> "%LOG%" 2>&1
echo ==== setup done %DATE% %TIME% >> "%LOG%"
type "%LOG%"
