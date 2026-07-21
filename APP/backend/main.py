import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from APP.backend.config import API_PORT
from APP.backend.auth import ensure_default_admin
from APP.backend.database import SessionLocal
from APP.backend.question_ingestion_worker import QuestionIngestionWorker
from APP.backend.api_errors import install_api_error_handlers
# 导入路由
from APP.backend.routers import auth_routes, file_routes, voice_routes, knowledge_routes, knowledge_atlas_routes, vl_chat_routes, personalization_routes, feedback_routes, dashboard_routes, training_routes, training_workspace_routes, case_training_routes, deep_training_routes, agent_routes, learning_activity_routes, exam_learning_routes, question_workspace_routes

app = FastAPI(title="Health Multi-Agent API", version="3.0")
install_api_error_handlers(app)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth_routes.router, tags=["Auth"])
# app.include_router(chat_routes.router, tags=["Chat"])
app.include_router(file_routes.router, tags=["Files"])
app.include_router(voice_routes.router, tags=["Voice"])
app.include_router(knowledge_routes.router, prefix="/knowledge", tags=["Knowledge"])
app.include_router(knowledge_atlas_routes.router)
app.include_router(vl_chat_routes.router, tags=["VL Chat"])
app.include_router(personalization_routes.router)
app.include_router(feedback_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(training_routes.router)
app.include_router(training_workspace_routes.router)
app.include_router(case_training_routes.router)
app.include_router(deep_training_routes.router)
app.include_router(agent_routes.router)
app.include_router(learning_activity_routes.router)
app.include_router(exam_learning_routes.router)
app.include_router(question_workspace_routes.router)

@app.on_event("startup")
def ensure_admin_account():
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
    app.state.question_ingestion_worker = QuestionIngestionWorker(SessionLocal)
    app.state.question_ingestion_worker.start()


@app.on_event("shutdown")
def stop_question_ingestion_worker():
    worker = getattr(app.state, "question_ingestion_worker", None)
    if worker is not None:
        worker.stop()

if __name__ == "__main__":
    uvicorn.run("APP.backend.main:app", host="0.0.0.0", port=API_PORT, reload=False)
