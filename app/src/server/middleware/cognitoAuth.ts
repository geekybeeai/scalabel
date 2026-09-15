import { NextFunction, Request, RequestHandler, Response } from "express"
import { expressjwt } from "express-jwt"
import jwksRsa from "jwks-rsa"

import { ServerConfig } from "../../types/config"

const auth = (config: ServerConfig): RequestHandler => {
  if (config.cognito !== undefined) {
    const verify = expressjwt({
      secret: jwksRsa.expressJwtSecret({
        cache: true,
        rateLimit: true,
        jwksRequestsPerMinute: 5,
        jwksUri: `https://cognito-idp.${config.cognito.region}.amazonaws.com/${config.cognito.userPool}/.well-known/jwks.json`
      }),

      issuer: `https://cognito-idp.${config.cognito.region}.amazonaws.com/${config.cognito.userPool}`,
      algorithms: ["RS256"]
    })
    // express-jwt v8 middleware is async; express 4 expects a void handler
    return (request: Request, response: Response, next: NextFunction) => {
      void verify(request, response, next)
    }
  } else {
    return (_request: Request, _response: Response, next: NextFunction) =>
      next()
  }
}

export default auth
