// Jest 26 cannot resolve the `node:` scheme; map node:crypto here.
module.exports = require("crypto")
