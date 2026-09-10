Output assets/demo.gif

Set Shell "zsh"
Set FontSize 18
Set Width 1200
Set Height 650
Set Padding 20
Set WindowBar Colorful
Set WindowBarSize 40
Set BorderRadius 8
Set Theme "Dracula"
Set TypingSpeed 60ms

Hide
Type `yp() { python3.11 yp.py "$@"; }`
Enter
Type `clear`
Enter
Show

Sleep 500ms
Type `yp "로파이 재즈 힙합"`
Enter

# "검색 중..." 만 잠깐 보여주고, 실제 검색이 끝날 때까지의 빈 화면은 잘라낸다.
# 안 자르면 GIF의 절반이 아무 일도 안 일어나는 구간이 된다.
Sleep 1200ms
Hide
Sleep 8s
Show

Sleep 800ms
Down
Sleep 600ms
Enter

# 카드가 "스트림 연결 중..." 으로 떴다가 재생으로 넘어가는 구간. 이 데모의 본론이다.
Sleep 8s

Type "q"
Sleep 1500ms
Enter
Sleep 1s
