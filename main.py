from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
import pandas as pd
import io
import re

app = FastAPI()

# --- CORS SETUP (Crucial for Next.js to talk to Python) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace "*" with your Vercel domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def parse_nigerian_statement(pdf_file):
    """
    Extracts 'Credit' column from GTBank/Zenith/Access style PDFs.
    Returns: Total Income (Sum of Credits)
    """
    total_credit = 0.0
    
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            # 1. Extract tables. pdfplumber is good at finding grid lines
            tables = page.extract_tables()
            
            for table in tables:
                # 2. Convert to Pandas DataFrame for easy filtering
                df = pd.DataFrame(table)
                
                # 3. Clean up: The first row is usually headers. 
                # We normalize headers to lowercase to find 'credit' easily.
                new_header = df.iloc[0].str.lower() 
                df = df[1:] 
                df.columns = new_header

                # 4. Find the 'credit' column (some banks call it 'cr' or 'credit amount')
                # This regex looks for any column name containing "credit" or "cr"
                credit_col = [c for c in df.columns if c and ("credit" in c or "cr" in c)]
                
                if credit_col:
                    target_col = credit_col[0] # Take the first match
                    
                    # 5. Sum up the values
                    for amount in df[target_col]:
                        if amount:
                            # Clean string: Remove commas, 'N', spaces, etc.
                            # e.g., "50,000.00" -> 50000.00
                            clean_amount = re.sub(r'[^\d.]', '', str(amount))
                            try:
                                val = float(clean_amount)
                                total_credit += val
                            except ValueError:
                                continue # Skip non-numeric rows (like dates or empty strings)
                                
    return total_credit

@app.post("/analyze")
async def analyze_statement(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(400, "File must be a PDF")
    
    # Read file into memory
    content = await file.read()
    file_stream = io.BytesIO(content)
    
    try:
        income = parse_nigerian_statement(file_stream)
        return {
            "filename": file.filename,
            "total_income": income,
            "is_creditworthy": income > 200000, # Example logic: > 200k/mo
            "message": "Analysis successful"
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/")
def home():
    return {"status": "FlexRent Parser is Live"}