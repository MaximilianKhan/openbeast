package main

import (
	"bufio"
	"fmt"
	"os"
)

func isVowel(c byte) bool {
	switch c | 0x20 {
	case 'a', 'e', 'i', 'o', 'u':
		return true
	}
	return false
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	for scanner.Scan() {
		line := scanner.Bytes()
		count := 0
		for _, c := range line {
			if isVowel(c) {
				count++
			}
		}
		fmt.Fprintln(writer, count)
	}
}
