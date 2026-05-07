package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func run(code, inp string) string {
	pairs := make(map[int]int)
	stack := []int{}
	for i, ch := range code {
		if ch == '[' {
			stack = append(stack, i)
		} else if ch == ']' {
			j := stack[len(stack)-1]; stack = stack[:len(stack)-1]
			pairs[i] = j; pairs[j] = i
		}
	}
	tape := make([]byte, 30000)
	ptr, ip, ipos := 0, 0, 0
	var out strings.Builder
	for ip < len(code) {
		c := code[ip]
		switch c {
		case '>': ptr++
		case '<': ptr--
		case '+': tape[ptr]++
		case '-': tape[ptr]--
		case '.': out.WriteByte(tape[ptr])
		case ',':
			if ipos < len(inp) { tape[ptr] = inp[ipos] } else { tape[ptr] = 0 }
			ipos++
		case '[':
			if tape[ptr] == 0 { ip = pairs[ip] }
		case ']':
			if tape[ptr] != 0 { ip = pairs[ip] }
		}
		ip++
	}
	return out.String()
}

func main() {
	in := bufio.NewReader(os.Stdin)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	readLine := func() string {
		line, _ := in.ReadString('\n')
		return strings.TrimRight(line, "\n")
	}
	T, _ := strconv.Atoi(readLine())
	for k := 0; k < T; k++ {
		code := readLine()
		inp := readLine()
		fmt.Fprintln(out, run(code, inp))
	}
}
