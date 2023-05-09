import random
import string

let = string.ascii_letters
num = string.digits
pun = string.punctuation

def createPass(elen, plen):
    email = [random.choice(let +num) for i in range(elen)]
    passw = [random.choice(let +num + pun) for i in range(plen)]
    finmail = ''.join(email)
    finpass = ''.join(passw)
    print("Email: " + finmail + "@gmail.com")
    print("Password: " + finpass)

elength = int(input("Email length:"))
plength = int(input("Password length:"))

createPass(elength, plength)
