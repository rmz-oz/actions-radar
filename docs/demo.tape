# Regenerate with: vhs docs/demo.tape
Output docs/demo.gif
Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 330
Set Padding 18
Set Theme "Builtin Dark"
Set TypingSpeed 35ms
Set WaitTimeout 60s
Set WaitPattern /^\$\s*$/
Hide
Type "export PS1='$ ' PATH=$PWD:$PATH; alias actions-radar=./radar.py; clear" Enter
Show
Type "actions-radar watch pallets/flask --sha 329c32b --log-lines 0" Sleep 400ms Enter
Wait
Sleep 2s
Type "actions-radar watch rmz-oz/actions-radar --sha HEAD" Sleep 400ms Enter
Wait
Sleep 3s
