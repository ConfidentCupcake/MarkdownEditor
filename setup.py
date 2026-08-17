# setup.py
# Build script for compiling the Cython lexer.
# Run python stup.py build_ext --inplace

# This produces:
#       Windows: lexer_fast.cp3xx-win_amd64.pyd
#       Linux: lexer_fast.cpython-3xx-x86_64-inux-gnu.so


from setuptools import setup
from Cython.Build import cythonize

setup(
    ext_modules=cythonize(
        "lexer_fast.pyx",
        compiler_directives={
            # boundscheck=False: disable IndexError checking oin array access
            # We manually check bounds, so thi is safe. Gives ~20% speedup.
            "boundscheck": False,
            
            # wraparound=False: disable negative indexing (text[-1])
            # We never use negative indicies. Gives ~10% speedup.
            "wraparound": False,
            
            # cdivision=True: use C division instead of Python division
            # Faster for integer division.
            "cdivision": True,
            
            # language_level=3: use Python 3 semantics for strings/bytes
            "language_level": "3",
        }
    )
)