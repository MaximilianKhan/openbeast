#!/bin/bash
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4
cd "$(dirname "$0")"
python3 collect_points.py compute q38-27b-Q2K-ours --workers 2 --threads 4
