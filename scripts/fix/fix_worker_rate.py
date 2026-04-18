import sys
sys.stdout.reconfigure(encoding='utf-8')

f = open('templates/workers/index.html', 'r', encoding='utf-8')
c = f.read()
f.close()

c = c.replace('300 ₽', '150 ₽')
c = c.replace('value = 300', 'value = 150')
c = c.replace('|| 300', '|| 150')

f = open('templates/workers/index.html', 'w', encoding='utf-8')
f.write(c)
f.close()

print('Updated 300->150')
