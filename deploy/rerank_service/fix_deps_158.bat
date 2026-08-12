@echo off
setlocal
set VENV=D:\rag_rerank\venv
set LOG=D:\rag_rerank\fix_deps.log
set PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
call "%VENV%\Scripts\activate.bat"
echo ==== fix start %DATE% %TIME% > "%LOG%"
pip install "transformers>=4.44.2,<5.0.0" "sentence-transformers>=3.0.0,<4.0.0" -i %PIP_INDEX% >> "%LOG%" 2>&1
python -c "import transformers; print(transformers.__version__)" >> "%LOG%" 2>&1
echo ==== fix done %DATE% %TIME% >> "%LOG%"
type "%LOG%"
