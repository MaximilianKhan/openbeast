package main

import (
	"bufio"
	"fmt"
	"os"
	"sort"
)

type point struct {
	x, y int64
}

func cross(o, a, b point) int64 {
	return (a.x-o.x)*(b.y-o.y) - (a.y-o.y)*(b.x-o.x)
}

func hull(pts []point) []point {
	sort.Slice(pts, func(i, j int) bool {
		if pts[i].x != pts[j].x {
			return pts[i].x < pts[j].x
		}
		return pts[i].y < pts[j].y
	})
	// dedupe
	uniq := pts[:0]
	var prev point
	for i, p := range pts {
		if i == 0 || p != prev {
			uniq = append(uniq, p)
			prev = p
		}
	}
	pts = uniq
	if len(pts) <= 1 {
		res := make([]point, len(pts))
		copy(res, pts)
		return res
	}

	lower := []point{}
	for _, p := range pts {
		for len(lower) >= 2 && cross(lower[len(lower)-2], lower[len(lower)-1], p) <= 0 {
			lower = lower[:len(lower)-1]
		}
		lower = append(lower, p)
	}
	upper := []point{}
	for i := len(pts) - 1; i >= 0; i-- {
		p := pts[i]
		for len(upper) >= 2 && cross(upper[len(upper)-2], upper[len(upper)-1], p) <= 0 {
			upper = upper[:len(upper)-1]
		}
		upper = append(upper, p)
	}
	res := append(lower[:len(lower)-1], upper[:len(upper)-1]...)
	return res
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	var t int
	fmt.Fscan(reader, &t)
	for ; t > 0; t-- {
		var n int
		fmt.Fscan(reader, &n)
		pts := make([]point, n)
		for i := 0; i < n; i++ {
			fmt.Fscan(reader, &pts[i].x, &pts[i].y)
		}
		h := hull(pts)
		fmt.Fprintln(writer, len(h))
		for _, p := range h {
			fmt.Fprintf(writer, "%d %d\n", p.x, p.y)
		}
	}
}
