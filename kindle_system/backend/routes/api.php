<?php

use Illuminate\Support\Facades\Route;
use App\Http\Controllers\Api\BookController;
use App\Http\Controllers\Api\JobController;

/*
|--------------------------------------------------------------------------
| Kindle Pulse API Routes
|--------------------------------------------------------------------------
*/

// 書籍関連
Route::get('/books',                 [BookController::class, 'index']);
Route::get('/books/{asin}/history',  [BookController::class, 'history']);
Route::post('/want',                 [BookController::class, 'want']);
Route::post('/purchase',             [BookController::class, 'purchase']);

// ジョブ管理（クローラーコンテナに転送）
Route::get('/status',  [JobController::class, 'status']);
Route::post('/run',    [JobController::class, 'run']);
Route::post('/stop',   [JobController::class, 'stop']);
