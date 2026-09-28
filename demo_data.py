from pathlib import Path
import pandas as pd

def frames() -> dict[str, pd.DataFrame]:
    donors=pd.DataFrame([
      ["Alex River","Austin","tx","78701","ACME","ENGINEER","D1"], ["Jordan Pine","Seattle","WA","98101","SELF","TEACHER","D2"],
      ["Casey Lake","Albany","NY","12207","COMMUNITY PAC","PAC","D3"], ["Morgan Reed","Miami","fl","33101","HEALTH CO","NURSE","D4"],
      ["Taylor Stone","Denver","CO","80202","UNION LOCAL","UNION","D5"], ["Riley North","Boston","MA","02108","ACME","ENGINEER","D6"]], columns=["contbr_nm","contbr_city","contbr_st","contbr_zip","contbr_employer","contbr_occupation","donor_id"])
    candidates=pd.DataFrame([["C1","Candidate Alpha"],["C2","Candidate Beta"]],columns=["cand_id","cand_nm"])
    committees=pd.DataFrame([["M1","C1"],["M2","C2"]],columns=["cmte_id","cand_id"])
    rows=[]
    vals=[("M1","D1",120,"01/15/2012"),("M1","D2",45,"02/02/2012"),("M2","D4",80,"02/18/2012"),("M2","D6",200,"03/04/2012"),("M2","D1",60,"03/20/2012"),("M1","D4",90,"04/11/2012"),("M1","D3",500,"04/22/2012"),("M2","D5",700,"05/02/2012")]
    for i,(m,d,a,dt) in enumerate(vals,1): rows.append([m,d,a,dt,"","","","SA11AI",str(1000+i)])
    contributions=pd.DataFrame(rows,columns=["cmte_id","donor_id","contb_receipt_amt","contb_receipt_dt","receipt_desc","memo_cd","memo_text","form_tp","file_num"])
    return {"donors":donors,"candidates":candidates,"committees":committees,"contributions":contributions}

def write(folder, tables: dict[str, pd.DataFrame] | None = None) -> Path:
    """Writes the given (or default demo) table set as CSVs into folder, for use by both the demo walkthrough and tests."""
    p=Path(folder); p.mkdir(parents=True,exist_ok=True)
    for name,df in (tables or frames()).items(): df.to_csv(p/f"{name}.csv",index=False)
    return p

def create(folder="demo_input"):
    p=write(folder)
    print(f"Created fictional demonstration files in {p.resolve()}")

if __name__=="__main__": create()
