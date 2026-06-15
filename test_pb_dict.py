import datetime
def sanitize_dict(d):
    for k, v in d.items():
        if isinstance(v, datetime.datetime):
            d[k] = v.strftime("%Y-%m-%d %H:%M:%S.000Z")
    return d

d = {'created': datetime.datetime(2026, 6, 14, 23, 24, 56)}
print(sanitize_dict(d))
