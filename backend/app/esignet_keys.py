"""Public JWK and stable key ID shared by provider bootstrap and client JWTs."""
import base64
import hashlib
import json


def public_jwk(private):
    numbers=private.public_key().public_numbers()
    def encode(number):
        return base64.urlsafe_b64encode(number.to_bytes((number.bit_length()+7)//8,'big')).decode().rstrip('=')
    key={'e':encode(numbers.e),'kty':'RSA','n':encode(numbers.n)}
    key['kid']=hashlib.sha256(json.dumps(key,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    key.update(use='sig',alg='PS256')
    return key
