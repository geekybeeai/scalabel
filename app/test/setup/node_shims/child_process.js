// Jest 26 cannot resolve the `node:` scheme; map node:child_process here.
module.exports = require("child_process")
