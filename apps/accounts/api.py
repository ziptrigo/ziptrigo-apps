from ninja import Router

from .routers import account, auth

router = Router()

router.add_router('/', account.router, tags=['Account'])
router.add_router('/auth/', auth.router, tags=['Authentication'])
