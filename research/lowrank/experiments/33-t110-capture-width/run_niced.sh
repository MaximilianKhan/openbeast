#!/bin/bash
# T1.10 remaining spectra, CPU-polite: nice 19, 2 workers x 4 BLAS threads
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4
cd "$(dirname "$0")"
for c in q38-27b-UD-IQ2S q38-27b-UD-Q2KXL q38-27b-UD-IQ3S h27b-IQ3XS; do
  python3 collect_points.py compute "$c" --workers 2 --threads 4
done
