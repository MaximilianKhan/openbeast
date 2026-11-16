package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
)

func accel(p [][]float64, mass []float64, n int) [][]float64 {
	a := make([][]float64, n)
	for i := range a {
		a[i] = []float64{0, 0}
	}
	for i := 0; i < n; i++ {
		for j := 0; j < n; j++ {
			if i == j {
				continue
			}
			dx := p[j][0] - p[i][0]
			dy := p[j][1] - p[i][1]
			r2 := dx*dx + dy*dy
			r := math.Sqrt(r2)
			if r > 1e-12 {
				f := mass[j] / (r2 * r)
				a[i][0] += f * dx
				a[i][1] += f * dy
			}
		}
	}
	return a
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }
	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		N, _ := strconv.Atoi(read())
		dt, _ := strconv.ParseFloat(read(), 64)
		steps, _ := strconv.Atoi(read())
		mass := make([]float64, N)
		pos := make([][]float64, N)
		vel := make([][]float64, N)
		for i := 0; i < N; i++ {
			m, _ := strconv.ParseFloat(read(), 64)
			x, _ := strconv.ParseFloat(read(), 64)
			y, _ := strconv.ParseFloat(read(), 64)
			vx, _ := strconv.ParseFloat(read(), 64)
			vy, _ := strconv.ParseFloat(read(), 64)
			mass[i] = m; pos[i] = []float64{x, y}; vel[i] = []float64{vx, vy}
		}
		a := accel(pos, mass, N)
		for s := 0; s < steps; s++ {
			for i := 0; i < N; i++ {
				pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt
				pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt
			}
			aNew := accel(pos, mass, N)
			for i := 0; i < N; i++ {
				vel[i][0] += 0.5 * (a[i][0] + aNew[i][0]) * dt
				vel[i][1] += 0.5 * (a[i][1] + aNew[i][1]) * dt
			}
			a = aNew
		}
		for i := 0; i < N; i++ {
			fmt.Fprintf(out, "%.6f %.6f %.6f %.6f\n", pos[i][0], pos[i][1], vel[i][0], vel[i][1])
		}
	}
}
