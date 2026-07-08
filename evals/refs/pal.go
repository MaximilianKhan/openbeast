package main

import (
	"bufio"
	"fmt"
	"os"
	"unicode"
)

func isPalindrome(line string) bool {
	var clean []rune
	for _, r := range line {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			clean = append(clean, unicode.ToLower(r))
		}
	}
	for i, j := 0, len(clean)-1; i < j; i, j = i+1, j-1 {
		if clean[i] != clean[j] {
			return false
		}
	}
	return true
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	for scanner.Scan() {
		if isPalindrome(scanner.Text()) {
			fmt.Fprintln(writer, "true")
		} else {
			fmt.Fprintln(writer, "false")
		}
	}
}
