@echo off
setlocal
set VENV=D:\rag_rerank\venv
set LOG=D:\rag_rerank\setup_cpu.log
set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

echo ==== setup_cpu start %DATE% %TIME% > "%LOG%"
if not exist "%VENV%\Scripts\python.exe" (
  D:\rag_rerank\Python311\python.exe -m venv "%VENV%" >> "%LOG%" 2>&1
)
call "%VENV%\Scripts\activate.bat"
python -m pip install -U pip -i %PIP_INDEX% >> "%LOG%" 2>&1
pip install fastapi uvicorn torch FlagEmbedding -i %PIP_INDEX% >> "%LOG%" 2>&1
python -c "import torch; print('cuda', torch.cuda.is_available()); print(torch.__version__)" >> "%LOG%" 2>&1
python -c "import fastapi, FlagEmbedding; print('deps-ok')" >> "%LOG%" 2>&1
echo ==== setup_cpu done %DATE% %TIME% >> "%LOG%"
type "%LOG%"
