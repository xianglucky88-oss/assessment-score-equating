def calculate(rows, payload):
    keys=payload.get("keys", {}); void=set(payload.get("void_items", [])); scale=payload.get("scale", {}); scores={}
    for row in rows:
        if row["item"] in void: continue
        bucket=scores.setdefault((row["student"], row["form"]), {"correct":0,"attempted":0}); bucket["attempted"]+=1; bucket["correct"] += row["answer"] == keys.get(row["item"])
    reports=[]
    for (student, form), b in sorted(scores.items()): reports.append({"student":student,"form":form,"raw":b["correct"],"scale":round(b["correct"]*scale.get(form,10),2),"attempted":b["attempted"]})
    return {"scores": reports, "void_items": sorted(void)}
