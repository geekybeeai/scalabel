// Jest 26 cannot resolve the `node:` scheme; map node:events here.
module.exports = require("events")
