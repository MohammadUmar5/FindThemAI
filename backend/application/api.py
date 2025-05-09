from fastapi import APIRouter, Depends, HTTPException, UploadFile, Form, File, status
from sqlalchemy.orm import Session
from fastapi.responses import Response
from typing import List
from application import models, schemas, crud, auth
from application.database import get_db
from application.face_detection import detect_faces
from application.face_recognition import get_face_embedding
from application.face_matching import match_face
import numpy as np
from base64 import b64encode
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Optional
from fastapi import Request


router = APIRouter(prefix="/api/v1")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# add this in argument to protect any route
# current_user: schemas.UserResponse = Depends(get_current_user)

# use this header to access protected endpoints
# Authorization: Bearer <JWT_TOKEN>

### AUTHENTICATION ENDPOINTS ###
@router.post("/register", response_model=schemas.UserResponse)
def register(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if crud.get_user_by_username(db, username):
        raise HTTPException(status_code=400, detail="Username already registered")
    
    user_data = schemas.UserCreate(username=username, password=password)
    return crud.create_user(db, user_data)

@router.post("/token", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = crud.authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    
    access_token = auth.create_access_token({"sub": user.username, "user_id": user.id})
    return {"access_token": access_token, "token_type": "bearer"}


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    # Allow preflight request through
    if request.method == "OPTIONS":
        return None

    if not token:
        raise HTTPException(status_code=401, detail="Token missing")

    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = crud.get_user_by_username(db, payload["sub"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user




@router.get("/protected")
def protected_route(current_user: schemas.UserResponse = Depends(get_current_user)):
    return {"message": f"Hello, {current_user.username}!"}


### ROOT ENDPOINT ###
@router.get("/")
def read_root():
    return {"message": " Welcome to AI Missing Person Identification"}

### USER ENDPOINTS ###
@router.get("/users/{user_id}", response_model=schemas.UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.get("/users", response_model=List[schemas.UserResponse])
def get_users(db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    return crud.get_users(db)


### MISSING PERSON ENDPOINTS ###
@router.post("/create-missing-person")
async def create_missing_person(
    name: str = Form(...),
    date_of_disappearance: str = Form(...),
    age: int = Form(...),
    last_seen_location: str = Form(...),
    home_address: str = Form(None),
    places_frequently_visited: str = Form(None),
    physical_description: str = Form(None),
    clothing_when_last_seen: str = Form(None),
    additional_notes: str = Form(None),
    issuer_mobile_number: str = Form(...),
    issuer_email_address: str = Form(...),
    issuer_name: str = Form(...),
    status: str = Form(None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: schemas.UserResponse = Depends(get_current_user)
):
    image_data = await image.read()
    new_person = schemas.MissingPersonCreate(
        name=name,
        date_of_disappearance=date_of_disappearance,
        age=age,
        last_seen_location=last_seen_location,
        home_address=home_address,
        places_frequently_visited=places_frequently_visited,
        physical_description=physical_description,
        clothing_when_last_seen=clothing_when_last_seen,
        additional_notes=additional_notes,
        issuer_mobile_number=issuer_mobile_number,
        issuer_email_address=issuer_email_address,
        issuer_name=issuer_name,
        status=status
    )
    created_person = crud.create_missing_person(db, new_person, image_data)
    return {"message": "Missing person entry created successfully", "id": created_person.id}

@router.get("/missing-persons/{person_id}", response_model=schemas.MissingPersonResponse)
def get_missing_person_info(person_id: int, db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    person = crud.get_missing_person(db, person_id)    
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if person.image:
        person.image = b64encode(person.image).decode('utf-8')
    return person

@router.get("/missing-persons/{person_id}/image")
def get_missing_person_image(person_id: int, db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    person = crud.get_missing_person(db, person_id)
    if not person or not person.image:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=person.image, media_type="image/jpeg")

@router.get("/missing-persons", response_model=List[schemas.MissingPersonResponse])
def get_missing_persons(db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    missing_persons = crud.get_missing_persons(db)
    
    for person in missing_persons:
        if person.image:
            person.image = b64encode(person.image).decode('utf-8')
    return missing_persons

@router.delete("/missing-persons/{person_id}")
def delete_missing_person(person_id: int, db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    deleted = crud.delete_missing_person(db, person_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"message": "Person deleted successfully"}

### FOUND PERSON ENDPOINTS ###
@router.post("/found-person", response_model= schemas.FoundPersonResponse)
async def get_found_person_info(image: UploadFile = File(...), db: Session = Depends(get_db), current_user: schemas.UserResponse = Depends(get_current_user)):
    try:
        image_data = await image.read()

        faces, image_np = detect_faces(image_data)

        if faces is None or len(faces) == 0:
            raise HTTPException(status_code=400, detail="No faces detected in the image")

        # For simplicity, process only the first detected face
        (x, y, w, h) = faces[0]
        face_pixels = image_np[y:y+h, x:x+w]  # Extract the face region

        embedding = get_face_embedding(face_pixels)
        if embedding is None:
            raise HTTPException(status_code=500, detail="Failed to generate face embedding")

        # Retrieve all stored embeddings from the database
        missing_persons = db.query(models.MissingPerson).all()
        if not missing_persons:
            raise HTTPException(status_code=404, detail="No missing persons in the database")

        result = match_face(missing_persons, embedding)

        if result:
            result['message'] = "Potential match found for missing person!"
            return result
        else:
            return {"message": "No match found for the uploaded face."}


    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error. Please try again.")