@echo off
setlocal
set VENV=D:\rag_rerank\venv
set LOG=D:\rag_rerank\setup2.log
set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
set TORCH_WHL=D:\rag_rerank\torch-2.6.0+cu124-cp311-cp311-win_amd64.whl

echo ==== setup2 start %DATE% %TIME% > "%LOG%"
call "%VENV%\Scripts\activate.bat"
python -m pip install -U pip -i %PIP_INDEX% >> "%LOG%" 2>&1
pip install fastapi uvicorn -i %PIP_INDEX% >> "%LOG%" 2>&1
if exist "%TORCH_WHL%" (
  echo installing local torch wheel >> "%LOG%"
  pip install "%TORCH_WHL%" -i %PIP_INDEX% >> "%LOG%" 2>&1
) else (
  echo TORCH WHEEL MISSING >> "%LOG%"
  exit /b 1
)
pip install FlagEmbedding -i %PIP_INDEX% >> "%LOG%" 2>&1
python -c "import torch; print('cuda', torch.cuda.is_available()); print(torch.__version__)" >> "%LOG%" 2>&1
python -c "import fastapi, FlagEmbedding; print('deps-ok')" >> "%LOG%" 2>&1
echo ==== setup2 done %DATE% %TIME% >> "%LOG%"
type "%LOG%"
