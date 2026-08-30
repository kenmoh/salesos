import random
import string
from datetime import datetime, timezone

def generate_random_timestamp_string(length=8):
    """
    Generate a random alphanumeric string combined with a timezone-aware timestamp.
    
    Args:
        length (int): Length of the random string portion. Default is 8.
    
    Returns:
        str: Format "<random_string>_<YYYY-MM-DD HH:MM:SS +ZZZZ>"
    """
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %z')
    return f"{random_str}_{timestamp}"